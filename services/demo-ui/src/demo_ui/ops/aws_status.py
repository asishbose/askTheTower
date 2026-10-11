"""The AWS pipeline checklist (doc 11 §2 "Pane 5", O3): seven read-only steps.

State comes only from files `make` writes and from unauthenticated GETs of the URLs in `deploy/.env.aws` (2 s each,
concurrently, the whole checklist cached 5 s). No bearer is ever sent, no AWS SDK or process is used, and nothing
raises to the view: a malformed file is that step's `fail` with the reason. The commands are text for the presenter
to copy into a terminal; the UI never runs them. No step's detail carries a URL from `.env.aws`.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import httpx

from demo_ui.config import read_env_file

State = Literal["todo", "done", "warn", "fail", "off"]
Probe = int | Literal["timeout", "unreachable", "skipped"]

ECR_SERVICES = frozenset(
    {"mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client"}
)  # SERVICES, mk/vars.mk
IMAGE_TAG = re.compile(r"[0-9a-f]{7,40}")
PROBE_TIMEOUT_S = 2.0
CACHE_S = 5.0
REPORT_ROW = "| End to end (ENV=aws) |"

COMMANDS = {
    1: "make ecr-up",
    2: "make push ENV=aws",
    3: "make plan && make deploy",
    4: "make cognito-users ENV=aws && make seed-aws ENV=aws",
    5: "make web-chat-sync ENV=aws && make web-chat-url ENV=aws",
    6: (
        'open: set demo_admin_cidrs = ["<your ip>/32"] in deploy/terraform/envs/aws.tfvars, then make plan && '
        "make deploy; close: demo_admin_cidrs = [], then make deploy"
    ),
    7: "make test-e2e ENV=aws && make test-report",
}
TITLES = {
    1: "ECR repositories",
    2: "Images pushed",
    3: "Deployed",
    4: "Users seeded",
    5: "Web chat live",
    6: "Admin path",
    7: "AWS test report",
}


@dataclass(frozen=True)
class Step:
    id: int
    title: str
    state: State
    command: str
    detail: str = ""


@dataclass(frozen=True)
class Checklist:
    steps: tuple[Step, ...]

    @property
    def headline(self) -> str:
        if all(s.state in ("todo", "off") for s in self.steps):  # step 6 is `off` until the admin path opens
            return "Nothing deployed yet"
        done = sum(s.state == "done" for s in self.steps)
        return f"{done} of {len(self.steps)} steps done"


@dataclass(frozen=True)
class Files:
    """Where the evidence lives, relative to the checkout (doc 11 §2 "Pane 5")."""

    ecr: Path
    image_tag: Path
    env_aws: Path
    users: Path
    seed: Path
    web_chat: Path
    report: Path

    @classmethod
    def under(cls, root: Path, env_aws: Path | None = None) -> Files:
        a = root / "artifacts"
        return cls(
            ecr=a / "tf-outputs-ecr.json",
            image_tag=a / "image-tag",
            env_aws=env_aws or root / "deploy" / ".env.aws",
            users=a / "cognito-users.json",
            seed=a / "seed-aws.json",
            web_chat=a / "web-chat-sync.json",
            report=a / "test-report.md",
        )


class Malformed(ValueError):
    """A file is there but cannot be read as the doc says; the message is shown."""


def _step(n: int, state: State, detail: str = "") -> Step:
    return Step(n, TITLES[n], state, COMMANDS[n], detail)


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise Malformed(f"{path.name} is not valid JSON") from e


def _value(outputs: Mapping[str, Any], key: str) -> Any:
    v = outputs.get(key)
    return v["value"] if isinstance(v, dict) and "value" in v else v  # `terraform output -json` wraps values


def step_ecr(f: Files) -> Step:
    if not f.ecr.is_file():
        return _step(1, "todo")
    data = _json(f.ecr)
    repos = _value(data, "ecr_repositories") if isinstance(data, dict) else None
    if not isinstance(repos, dict):
        return _step(1, "fail", "no ecr_repositories in tf-outputs-ecr.json")
    missing = sorted(ECR_SERVICES - set(repos))
    if missing:
        return _step(1, "fail", f"missing: {', '.join(missing)}")
    return _step(1, "done", f"{len(ECR_SERVICES)} repositories")


def step_images(f: Files) -> Step:
    if not f.image_tag.is_file():
        return _step(2, "todo")
    lines = f.image_tag.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) != 1 or not IMAGE_TAG.fullmatch(lines[0].strip()):
        return _step(2, "fail", "artifacts/image-tag is not one git tag")
    return _step(2, "done", f"tag {lines[0].strip()}")


def step_deployed(env: Mapping[str, str] | None, probe: Probe) -> Step:
    if env is None:
        return _step(3, "todo")
    if not env.get("TOWER_URL") or not env.get("BINDING_URL"):
        return _step(3, "fail", "deploy/.env.aws lacks TOWER_URL or BINDING_URL: run make outputs ENV=aws")
    if probe == 200:
        return _step(3, "done", "binding page healthz 200")
    return _step(3, "fail", f"deployed but not answering ({probe})")


def step_users(f: Files) -> Step:
    if not f.users.is_file():
        return _step(4, "todo")
    users = _json(f.users)
    if not isinstance(users, dict):
        return _step(4, "fail", "cognito-users.json is not an object")
    subs = [str(users.get(k, "")) for k in ("asish", "mom")]
    if not all(subs) or any(s.startswith("user-") for s in subs):
        return _step(4, "fail", "cognito-users.json lacks asish and mom as Cognito subs")
    if not f.seed.is_file():
        return _step(4, "warn", "users created, seed not run")
    seed = _json(f.seed)
    if not isinstance(seed, dict):
        return _step(4, "fail", "seed-aws.json is not an object")
    if f.seed.stat().st_mtime < f.users.stat().st_mtime:
        return _step(4, "warn", "users changed since the seed: seed again")
    people = seed.get("people")
    if (
        seed.get("mock_loaded") is not True
        or not isinstance(people, list)
        or not {"asish", "mom"} <= set(people)
    ):
        return _step(4, "warn", "the seed did not load the mock or both people")
    return _step(4, "done", "asish and mom seeded")


def step_web_chat(f: Files, env: Mapping[str, str] | None, probe: Probe) -> Step:
    if not f.web_chat.is_file():
        return _step(5, "todo")
    sync = _json(f.web_chat)
    url = sync.get("url") if isinstance(sync, dict) else None
    if not isinstance(url, str) or not url:
        return _step(5, "fail", "web-chat-sync.json has no url")
    current = (env or {}).get("WEB_CHAT_URL", "")
    if url.rstrip("/") != current.rstrip("/"):
        return _step(5, "warn", "re-deployed since: sync again")
    if probe == 200:
        return _step(5, "done", "the page answers 200")
    return _step(5, "fail", f"the page does not answer ({probe})")


def step_admin(env: Mapping[str, str] | None, probe: Probe) -> Step:
    """The probe carries no header (O3), so on an open path the gateway answers 401 (decision 1: the gate needs
    `DEMO_ADMIN_BEARER` as well as a listed address): that proves the route exists, not that this address is listed;
    a wrong address shows on first use (Reset: "mock load: 403"). 200 is a deployment from before decision 1."""
    if not env or not env.get("MOCK_URL"):
        return _step(6, "off", "closed (demo_admin_cidrs = [])")
    if probe in (200, 401):
        if not env.get("DEMO_ADMIN_BEARER"):
            return _step(
                6, "warn", "open, but deploy/.env.aws has no DEMO_ADMIN_BEARER: run make outputs ENV=aws"
            )
        return _step(6, "done", "admin on")
    if probe == 403:
        return _step(6, "warn", "your IP is not in demo_admin_cidrs")
    return _step(6, "fail", f"admin path not answering ({probe})")


def step_report(f: Files) -> Step:
    if not f.report.is_file():
        return _step(7, "todo")
    row = next(
        (ln for ln in f.report.read_text(encoding="utf-8").splitlines() if ln.startswith(REPORT_ROW)), None
    )
    if row is None:
        return _step(7, "todo", "no ENV=aws row in the report")
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    if len(cells) < 6 or "not run here" in row or cells[1] in {"–", "-", ""}:
        return _step(7, "todo", "not run here")
    try:
        passed, failed, errors, _skipped, total = (int(c) for c in cells[1:6])
    except ValueError as e:
        raise Malformed("the ENV=aws row of test-report.md has no counts") from e
    if failed or errors:
        return _step(7, "fail", f"{failed} failed, {errors} errors of {total}")
    if total <= 0:
        return _step(7, "todo", "not run here")
    return _step(7, "done", f"{passed} passed of {total}")


def _after_predecessor(steps: list[Step]) -> tuple[Step, ...]:
    """'after step n' when the step before is not done (`off` counts as settled: the admin path is optional)."""
    out = [steps[0]]
    for prev, step in zip(steps, steps[1:], strict=False):
        if prev.state in ("done", "off"):
            out.append(step)
        else:
            note = f"after step {prev.id}"
            out.append(replace(step, detail=f"{note}; {step.detail}" if step.detail else note))
    return tuple(out)


class AwsStatus:
    """Builds the checklist; `http` is an httpx client with no auth headers (built in the composition root)."""

    def __init__(
        self,
        files: Files,
        http: httpx.AsyncClient,
        *,
        timeout_s: float = PROBE_TIMEOUT_S,
        cache_s: float = CACHE_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.files = files
        self.http = http
        self.timeout_s = timeout_s
        self.cache_s = cache_s
        self.monotonic = monotonic
        self._cached: tuple[float, Checklist] | None = None

    async def checklist(self, *, fresh: bool = False) -> Checklist:
        now = self.monotonic()
        if not fresh and self._cached is not None and now - self._cached[0] < self.cache_s:
            return self._cached[1]
        result = await self._build()
        self._cached = (now, result)
        return result

    async def _build(self) -> Checklist:
        env = self._env()
        env_map = env if isinstance(env, dict) else None
        urls = {
            "binding": f"{env_map['BINDING_URL'].rstrip('/')}/healthz"
            if env_map and env_map.get("BINDING_URL")
            else "",
            "web_chat": env_map.get("WEB_CHAT_URL", "") if env_map else "",
            "admin": f"{env_map['MOCK_URL'].rstrip('/')}/_admin/clock"
            if env_map and env_map.get("MOCK_URL")
            else "",
        }
        names = list(urls)
        results = await asyncio.gather(*(self._probe(urls[n]) for n in names))
        probes: dict[str, Probe] = dict(zip(names, results, strict=True))
        f = self.files
        builders: list[tuple[int, Callable[[], Step]]] = [
            (1, lambda: step_ecr(f)),
            (2, lambda: step_images(f)),
            (3, lambda: _env_step(3, env) or step_deployed(env_map, probes["binding"])),
            (4, lambda: step_users(f)),
            (5, lambda: _env_step(5, env) or step_web_chat(f, env_map, probes["web_chat"])),
            (6, lambda: _env_step(6, env) or step_admin(env_map, probes["admin"])),
            (7, lambda: step_report(f)),
        ]
        return Checklist(_after_predecessor([_safely(n, build) for n, build in builders]))

    def _env(self) -> dict[str, str] | Malformed | None:
        if not self.files.env_aws.is_file():
            return None
        try:
            return read_env_file(self.files.env_aws)
        except (OSError, UnicodeDecodeError, ValueError):
            return Malformed("deploy/.env.aws cannot be read")

    async def _probe(self, url: str) -> Probe:
        if not url:
            return "skipped"
        try:
            r = await self.http.get(url, timeout=self.timeout_s, follow_redirects=False)
        except httpx.TimeoutException:
            return "timeout"
        except (httpx.HTTPError, ValueError):
            return "unreachable"
        return r.status_code


def _env_step(n: int, env: dict[str, str] | Malformed | None) -> Step | None:
    return _step(n, "fail", str(env)) if isinstance(env, Malformed) else None


def _safely(n: int, build: Callable[[], Step]) -> Step:
    """A step never raises to the view (doc 11 §9): a bad file is that step's `fail`, with the reason."""
    try:
        return build()
    except Malformed as e:
        return _step(n, "fail", str(e))
    except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError) as e:
        return _step(n, "fail", f"cannot read the evidence ({type(e).__name__})")
