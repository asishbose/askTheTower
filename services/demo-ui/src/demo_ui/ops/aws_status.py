"""The AWS pipeline stepper (doc 11 §2 "AWS pipeline", T2, T6): fourteen steps, the admin box, teardown, the banner.

State comes from the files `make` writes, from unauthenticated GETs of the URLs in `deploy/.env.aws` (2 s each,
concurrent, cached 5 s, never a bearer) and from the quiet probes' answers, which are *injected* (`ops/probes.py`
runs them): this module runs no process and imports no AWS SDK. Nothing raises to the view: a malformed file is that
step's `fail` with the reason. Evidence is never a secret: names, counts, times, the public `binding_url` and
`web_chat_url`; never an admin or execute-api URL, a bearer, a key, a sub or the `/32`.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from demo_ui.config import read_env_file
from demo_ui.ops import plan
from demo_ui.ops.allowlist import TARGETS, OpsTarget, by_name
from demo_ui.ops.probes import ProbeResult, ProbeSnapshot, TfState, dashed, is_account
from demo_ui.ops.transcript import LastRun, Transcripts

State = Literal["todo", "done", "warn", "fail", "off"]
Get = int | Literal["timeout", "unreachable", "skipped"]

ECR_SERVICES = frozenset({"mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client"})  # mk/vars.mk
IMAGE_TAG = re.compile(r"[0-9a-f]{7,40}")
NAME = re.compile(r"[a-z]{1,32}")
GRANT = re.compile(r"[a-z]{1,32}>[a-z]{1,32}:[a-z_]{1,32}")
MEASURED_ROW = re.compile(r"^\|[^|]+\|\s*\d+\s*\|\s*[\d.]+\s*\|\s*[\d.]+\s*\|", re.M)
GET_TIMEOUT_S = 2.0
CACHE_S = 5.0
REPORT_ROW = "| End to end (ENV=aws) |"
NOT_DEPLOYED = "not deployed: evidence from an earlier deployment ignored"
COGNITO_NEEDED = "set COGNITO_PASSWORD_ASISH and COGNITO_PASSWORD_MOM in .env"
RESTART = "after Open or Close: restart the UI so the Control room uses the new bearer"
# deployment-agentcore §4: the fixed idle cost of what `make deploy` creates. The UI never reads billing.
COST = "≈ $0.06/h idle, ≈ $1.40/day (deployment-agentcore §4)"
PIPELINE: tuple[str, ...] = tuple(t.name for t in TARGETS if t.group == "pipeline")
AFTER_DEPLOY = 4  # steps 4–14 describe a deployment


def fmt_time(at: datetime | None) -> str:
    return "" if at is None else at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


@dataclass(frozen=True)
class Step:
    n: int
    target: str
    title: str
    state: State
    evidence: str = ""
    detail: str = ""
    at: datetime | None = None
    chips: plan.PlanSummary | None = None

    @property
    def when(self) -> str:
        return fmt_time(self.at)


@dataclass(frozen=True)
class AdminBox:
    state: State
    detail: str
    tfvars: Literal["open", "closed", "absent", "unreadable"]
    cidr: str  # the checkip default for the field (its only place on screen); "" when unknown


@dataclass(frozen=True)
class Teardown:
    stack: Literal["in state", "not in state", "unknown"]
    ecr: str


@dataclass(frozen=True)
class Banner:
    text: str
    title: str = ""


@dataclass(frozen=True)
class Pipeline:
    steps: tuple[Step, ...]
    admin: AdminBox
    teardown: Teardown
    banner: Banner | None
    next_id: int | None

    @property
    def headline(self) -> str:
        done = sum(s.state == "done" for s in self.steps)
        return "Nothing deployed yet" if done == 0 else f"{done} of {len(self.steps)} steps done"

    @property
    def done_targets(self) -> frozenset[str]:
        return frozenset(s.target for s in self.steps if s.state == "done")


@dataclass(frozen=True)
class Files:
    """Where the evidence lives, relative to the checkout (doc 11 §3 "Ops" file reads)."""

    ecr: Path
    image_tag: Path
    plan: Path
    tf_outputs: Path
    env_aws: Path
    users: Path
    seed: Path
    web_chat: Path
    latency: Path
    report: Path
    tfvars: Path

    @classmethod
    def under(cls, root: Path, env_aws: Path | None = None) -> Files:
        a = root / "artifacts"
        return cls(
            ecr=a / "tf-outputs-ecr.json",
            image_tag=a / "image-tag",
            plan=a / "terraform-plan.txt",
            tf_outputs=a / "tf-outputs.json",
            env_aws=env_aws or root / "deploy" / ".env.aws",
            users=a / "cognito-users.json",
            seed=a / "seed-aws.json",
            web_chat=a / "web-chat-sync.json",
            latency=a / "latency-aws.md",
            report=a / "test-report.md",
            tfvars=root / "deploy" / "terraform" / "envs" / "aws.tfvars",
        )


class Malformed(ValueError):
    """A file is there but cannot be read as the doc says; the message is shown."""


TARGET_OF = by_name()


def _label(target: str) -> str:
    return TARGET_OF[target].label


def _step(n: int, state: State, evidence: str = "", detail: str = "", at: datetime | None = None) -> Step:
    target = PIPELINE[n - 1]
    return Step(n, target, _label(target), state, evidence, detail, at)


def mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


def _newer(a: Path, b: Path) -> bool:
    ta, tb = mtime(a), mtime(b)
    return ta is not None and (tb is None or ta >= tb)


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise Malformed(f"{path.name} is not valid JSON") from e


def _object(path: Path) -> dict[str, Any]:
    data = _json(path)
    if not isinstance(data, dict):
        raise Malformed(f"{path.name} is not an object")
    return data


def _value(outputs: Mapping[str, Any], key: str) -> Any:
    v = outputs.get(key)
    return v["value"] if isinstance(v, dict) and "value" in v else v  # `terraform output -json` wraps values


def _names(values: Any) -> list[str]:
    return [v for v in values if isinstance(v, str) and NAME.fullmatch(v)] if isinstance(values, list) else []


# --- the fourteen steps ---------------------------------------------------------------------------------------


def step_ecr(f: Files, ecr: ProbeResult[tuple[str, ...]] | None) -> Step:
    if not f.ecr.is_file():
        return _step(1, "todo")
    repos = _value(_object(f.ecr), "ecr_repositories")
    if not isinstance(repos, dict):
        return _step(1, "fail", detail="no ecr_repositories in tf-outputs-ecr.json")
    missing = sorted(ECR_SERVICES - set(repos))
    if missing:
        return _step(1, "fail", detail=f"missing: {', '.join(missing)}")
    at = mtime(f.ecr)
    if ecr is None or not ecr.ok or ecr.value is None:
        return _step(1, "done", ", ".join(sorted(ECR_SERVICES)), at=at)
    names = sorted(n for n in ecr.value if n.rsplit("/", 1)[-1] in ECR_SERVICES)
    found = {n.rsplit("/", 1)[-1] for n in names}
    if not found:
        return _step(1, "todo", detail="file is stale: ECR lists none of the five", at=at)
    if found != ECR_SERVICES:
        return _step(1, "fail", detail=f"missing in ECR: {', '.join(sorted(ECR_SERVICES - found))}", at=at)
    return _step(1, "done", ", ".join(names), at=at)


def step_push(f: Files) -> Step:
    if not f.image_tag.is_file():
        return _step(2, "todo")
    lines = f.image_tag.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) != 1 or not IMAGE_TAG.fullmatch(lines[0].strip()):
        return _step(2, "fail", detail="artifacts/image-tag is not one git tag")
    return _step(2, "done", f"tag {lines[0].strip()}", at=mtime(f.image_tag))


def step_plan(f: Files) -> Step:
    if not f.plan.is_file():
        return _step(3, "todo")
    at = mtime(f.plan)
    summary = plan.read(f.plan)
    if summary is None:
        return _step(3, "warn", detail="summary not found", at=at)
    chips = f"+{summary.add} ~{summary.change} −{summary.destroy}"
    if not plan.is_current(f.plan, f.image_tag, f.tfvars):
        return replace(
            _step(3, "warn", chips, "inputs changed since the plan: plan again", at), chips=summary
        )
    return replace(_step(3, "done", chips, at=at), chips=summary)


def public_urls(f: Files, tf: TfState | None) -> dict[str, str]:
    """`binding_url` and `web_chat_url` (public pages), from the `tf` probe or `tf-outputs.json`; nothing else."""
    if tf is not None:
        return {k: v for k, v in tf.outputs.items() if k in ("binding_url", "web_chat_url")}
    if not f.tf_outputs.is_file():
        return {}
    data = _object(f.tf_outputs)
    return {k: v for k in ("binding_url", "web_chat_url") if isinstance(v := _value(data, k), str) and v}


def in_state(f: Files, tf: TfState | None) -> bool:
    if tf is not None:
        return tf.in_state
    return f.tf_outputs.is_file() and bool(_object(f.tf_outputs))


def step_deploy(f: Files, tf: TfState | None, healthz: Get) -> Step:
    if not in_state(f, tf):
        return _step(4, "todo")
    urls = public_urls(f, tf)
    evidence = " · ".join(urls[k] for k in ("binding_url", "web_chat_url") if k in urls)
    at = mtime(f.tf_outputs)
    if healthz != 200:
        return _step(4, "fail", evidence, f"deployed but not answering ({healthz})", at)
    return _step(4, "done", evidence, at=at)


def step_outputs(f: Files, env: Mapping[str, str] | None) -> Step:
    if env is None:
        return _step(5, "todo")
    at = mtime(f.env_aws)
    if not env.get("TOWER_URL") or not env.get("BINDING_URL"):
        return _step(5, "fail", detail="deploy/.env.aws lacks TOWER_URL or BINDING_URL", at=at)
    admin = "on" if env.get("MOCK_URL") and env.get("DEMO_ADMIN_BEARER") else "off"
    evidence = f"{len(env)} keys · admin {admin}"
    if not _newer(f.env_aws, f.tf_outputs):
        return _step(5, "warn", evidence, "outputs changed: write it again", at)
    return _step(5, "done", evidence, at=at)


def step_users(f: Files) -> Step:
    if not f.users.is_file():
        return _step(6, "todo")
    users = _object(f.users)
    subs = [str(users.get(k, "")) for k in ("asish", "mom")]
    if not all(subs) or any(s.startswith("user-") for s in subs):
        return _step(6, "fail", detail="cognito-users.json lacks asish and mom as Cognito subs")
    return _step(6, "done", "2 users: asish, mom", at=mtime(f.users))


def step_seed(f: Files) -> Step:
    if not f.seed.is_file():
        return _step(7, "todo")
    seed = _object(f.seed)
    at = mtime(f.seed)
    people = _names(seed.get("people"))
    if seed.get("mock_loaded") is not True or not {"asish", "mom"} <= set(people):
        return _step(7, "warn", detail="the seed did not load the mock or both people", at=at)
    if f.users.is_file() and not _newer(f.seed, f.users):
        return _step(7, "warn", detail="users changed since the seed: seed again", at=at)
    grants = seed.get("grants")
    shown = (
        [g for g in grants if isinstance(g, str) and GRANT.fullmatch(g)] if isinstance(grants, list) else []
    )
    evidence = (
        f"bound: {', '.join(_names(seed.get('bound'))) or 'none'} · grants: {', '.join(shown) or 'none'}"
    )
    return _step(7, "done", evidence, at=at)


def step_web_chat_sync(f: Files, env: Mapping[str, str] | None) -> Step:
    if not f.web_chat.is_file():
        return _step(8, "todo")
    sync = _object(f.web_chat)
    url = sync.get("url")
    if not isinstance(url, str) or not url:
        return _step(8, "fail", detail="web-chat-sync.json has no url")
    at = mtime(f.web_chat)
    files = sync.get("files")
    count = len(files) if isinstance(files, list) else files if isinstance(files, int) else 0
    if url.rstrip("/") != (env or {}).get("WEB_CHAT_URL", "").rstrip("/"):
        return _step(8, "warn", f"{count} files uploaded", "re-deployed since: sync again", at)
    return _step(8, "done", f"{count} files uploaded", at=at)


def step_web_chat_url(sync: Step, env: Mapping[str, str] | None, page: Get, last: LastRun | None) -> Step:
    at = last.at if last else None
    if sync.state != "done":
        return _step(9, "todo", at=at)
    url = (env or {}).get("WEB_CHAT_URL", "")
    if page != 200:
        return _step(9, "fail", detail=f"the page does not answer ({page})", at=at)
    return _step(9, "done", url, at=at)


def step_bind_asish(f: Files) -> Step:
    if not f.seed.is_file():
        return _step(10, "todo")
    if {"asish", "mom"} <= set(_names(_object(f.seed).get("bound"))):
        return _step(10, "done", "bound: asish, mom", at=mtime(f.seed))
    return _step(10, "todo")


def step_transcript(n: int, last: LastRun | None, ok: str) -> Step:
    if last is None:
        return _step(n, "todo")
    if last.state == "pass":
        return _step(n, "done", f"{ok} · {fmt_time(last.at)}", at=last.at)
    if last.state == "fail":
        return _step(n, "fail", detail=f"last run failed · {fmt_time(last.at)}", at=last.at)
    return _step(n, "todo", detail=f"last run stopped · {fmt_time(last.at)}", at=last.at)


def step_latency(f: Files) -> Step:
    if not f.latency.is_file():
        return _step(12, "todo")
    text = f.latency.read_text(encoding="utf-8", errors="replace")
    if "not measured" in text.lower() or not MEASURED_ROW.search(text):
        return _step(12, "todo", detail="not measured")
    at = mtime(f.latency)
    return _step(12, "done", f"measured · {fmt_time(at)}", at=at)


def step_report(f: Files) -> Step:
    if not f.report.is_file():
        return _step(14, "todo")
    row = next(
        (ln for ln in f.report.read_text(encoding="utf-8").splitlines() if ln.startswith(REPORT_ROW)), None
    )
    if row is None:
        return _step(14, "todo", detail="no ENV=aws row in the report")
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    if len(cells) < 6 or "not run here" in row or cells[1] in {"–", "-", ""}:
        return _step(14, "todo", detail="not run here")
    try:
        passed, failed, errors, _skipped, total = (int(c) for c in cells[1:6])
    except ValueError as e:
        raise Malformed("the ENV=aws row of test-report.md has no counts") from e
    if failed or errors:
        return _step(14, "fail", detail=f"{failed} failed, {errors} errors of {total}")
    if total <= 0:
        return _step(14, "todo", detail="not run here")
    return _step(14, "done", f"{passed} passed of {total}", at=mtime(f.report))


# --- the admin box, teardown, banner, next step -------------------------------------------------------------


def tfvars_state(path: Path) -> Literal["open", "closed", "absent", "unreadable"]:
    """`scripts/admin_cidr.py`'s rule, read-only: one `demo_admin_cidrs = [...]` line, empty or one address."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "absent"
    lines = [ln for ln in text.splitlines() if re.match(r"^\s*demo_admin_cidrs\s*=", ln)]
    if not lines:
        return "absent"
    value = lines[0].split("=", 1)[1].split("#", 1)[0].strip()
    if len(lines) > 1 or not (value.startswith("[") and value.endswith("]")):
        return "unreadable"
    entries = re.findall(r'"[^"]*"', value)
    return "closed" if not entries else "open" if len(entries) == 1 else "unreadable"


def admin_box(
    f: Files, env: Mapping[str, str] | None, clock: Get, checkip: ProbeResult[str] | None
) -> AdminBox:
    tfv = tfvars_state(f.tfvars)
    cidr = checkip.value if checkip is not None and checkip.ok and checkip.value else ""
    deployed_open = bool(env and env.get("MOCK_URL"))
    state: State
    if not deployed_open:
        state, detail = "off", "closed (demo_admin_cidrs = [])"
    elif clock in (200, 401) and not (env or {}).get("DEMO_ADMIN_BEARER"):
        state, detail = "warn", "open, but deploy/.env.aws has no DEMO_ADMIN_BEARER: run make outputs ENV=aws"
    elif clock in (200, 401):
        state, detail = "done", "admin on"
    elif clock == 403:
        state, detail = "warn", "your IP is not in demo_admin_cidrs"
    else:
        state, detail = "fail", f"admin path not answering ({clock})"
    if tfv == "open" and not deployed_open:
        state, detail = "warn", "tfvars open, deployment closed: run deploy"
    elif tfv == "closed" and deployed_open:
        state, detail = "warn", "tfvars closed, deployment open: run deploy"
    return AdminBox(state, detail, tfv, cidr)


def teardown(tf: ProbeResult[TfState] | None, ecr: ProbeResult[tuple[str, ...]] | None) -> Teardown:
    stack: Literal["in state", "not in state", "unknown"] = "unknown"
    if tf is not None and tf.ok and tf.value is not None:
        stack = "in state" if tf.value.in_state else "not in state"
    repos = "unknown"
    if ecr is not None and ecr.ok and ecr.value is not None:
        repos = f"{sum(n.rsplit('/', 1)[-1] in ECR_SERVICES for n in ecr.value)} repositories"
    return Teardown(stack, repos)


def banner(f: Files, steps: tuple[Step, ...], tf: TfState | None, admin: AdminBox) -> Banner | None:
    deployed = (
        tf.in_state
        if tf is not None
        else (f.tf_outputs.is_file() and steps[AFTER_DEPLOY - 1].state == "done")
    )
    if deployed:
        since = fmt_time(mtime(f.tf_outputs)) or "an unknown time"
        text = f"Deployed since {since}: {COST}. Down keeps ECR."
        if admin.state == "done":
            text += " Admin path open: close it after the demo."
        return Banner(text, "the time is when make outputs last wrote artifacts/tf-outputs.json")
    return None  # nothing deployed: no banner (ECR alone needs no note)


def next_step(steps: tuple[Step, ...]) -> int | None:
    """The first step, in order, that is not `done` and whose `after` steps all are; None when all are done."""
    done = {s.target for s in steps if s.state == "done"}
    for s in steps:
        if s.state != "done" and set(TARGET_OF[s.target].after) <= done:
            return s.n
    return None


def run_block(
    target: OpsTarget, pipeline: Pipeline, *, gate: str | None, busy: str | None, facts_cognito: int | None
) -> str:
    """Why an aws Run button is disabled ("" when it is enabled; doc 11 §2 "Next step")."""
    if gate:
        return gate
    if busy:
        return busy
    for after in target.after:
        if after not in pipeline.done_targets:
            return f"after {_label(after)}"
    if target.name == "aws-cognito-users" and facts_cognito != 2:
        return COGNITO_NEEDED
    return ""


# --- the builder ----------------------------------------------------------------------------------------------


def _safely(n: int, build: Callable[[], Step]) -> Step:
    """A step never raises to the view (doc 11 §9): a bad file is that step's `fail`, with the reason."""
    try:
        return build()
    except Malformed as e:
        return _step(n, "fail", detail=str(e))
    except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError, AttributeError) as e:
        return _step(n, "fail", detail=f"cannot read the evidence ({type(e).__name__})")


def _not_deployed(steps: list[Step]) -> list[Step]:
    return [s if s.n < AFTER_DEPLOY else _step(s.n, "todo", detail=NOT_DEPLOYED) for s in steps]


class AwsStatus:
    """Builds the pipeline; `http` is an httpx client with no auth headers (built in the composition root)."""

    def __init__(
        self,
        files: Files,
        http: httpx.AsyncClient,
        transcripts: Transcripts,
        *,
        timeout_s: float = GET_TIMEOUT_S,
        cache_s: float = CACHE_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.files = files
        self.http = http
        self.transcripts = transcripts
        self.timeout_s = timeout_s
        self.cache_s = cache_s
        self.monotonic = monotonic
        self._gets: tuple[float, dict[str, str], dict[str, Get]] | None = None

    async def pipeline(self, probes: ProbeSnapshot | None = None, *, fresh: bool = False) -> Pipeline:
        snap = probes or ProbeSnapshot()
        env = self._env()
        env_map = env if isinstance(env, dict) else None
        tf = snap.tf.value if snap.tf is not None and snap.tf.ok else None
        gets = await self._get_all(self._urls(env_map, tf), fresh=fresh)
        steps = self._steps(snap, env, env_map, tf, gets)
        if tf is not None and not tf.in_state:
            steps = _not_deployed(steps)
        admin = _safely_admin(lambda: admin_box(self.files, env_map, gets["admin"], snap.checkip))
        frozen = tuple(steps)
        return Pipeline(
            frozen,
            admin,
            teardown(snap.tf, snap.ecr),
            banner_safely(self.files, frozen, tf, admin),
            next_step(frozen),
        )

    def _steps(
        self,
        snap: ProbeSnapshot,
        env: dict[str, str] | Malformed | None,
        env_map: dict[str, str] | None,
        tf: TfState | None,
        gets: Mapping[str, Get],
    ) -> list[Step]:
        f, last = self.files, self.transcripts.last
        builders: list[tuple[int, Callable[[], Step]]] = [
            (1, lambda: step_ecr(f, snap.ecr)),
            (2, lambda: step_push(f)),
            (3, lambda: step_plan(f)),
            (4, lambda: step_deploy(f, tf, gets["binding"])),
            (5, lambda: _env_step(5, env) or step_outputs(f, env_map)),
            (6, lambda: step_users(f)),
            (7, lambda: step_seed(f)),
            (8, lambda: _env_step(8, env) or step_web_chat_sync(f, env_map)),
        ]
        steps = [_safely(n, build) for n, build in builders]
        steps.append(
            _safely(9, lambda: step_web_chat_url(steps[7], env_map, gets["web_chat"], last(PIPELINE[8])))
        )
        steps += [
            _safely(10, lambda: step_bind_asish(f)),
            _safely(11, lambda: step_transcript(11, last("aws-demo"), "demo ok")),
            _safely(12, lambda: step_latency(f)),
            _safely(13, lambda: step_transcript(13, last("aws-test-e2e"), "pass")),
            _safely(14, lambda: step_report(f)),
        ]
        return steps

    def _urls(self, env: Mapping[str, str] | None, tf: TfState | None) -> dict[str, str]:
        e = env or {}
        binding = e.get("BINDING_URL") or (tf.outputs.get("binding_url", "") if tf is not None else "")
        return {
            "binding": f"{binding.rstrip('/')}/healthz" if binding else "",
            "web_chat": e.get("WEB_CHAT_URL", ""),
            "admin": f"{e['MOCK_URL'].rstrip('/')}/_admin/clock" if e.get("MOCK_URL") else "",
        }

    async def _get_all(self, urls: dict[str, str], *, fresh: bool) -> dict[str, Get]:
        now = self.monotonic()
        cached = self._gets
        if not fresh and cached is not None and now - cached[0] < self.cache_s and cached[1] == urls:
            return cached[2]
        names = list(urls)
        results = await asyncio.gather(*(self._get(urls[n]) for n in names))
        gets: dict[str, Get] = dict(zip(names, results, strict=True))
        self._gets = (now, dict(urls), gets)
        return gets

    def _env(self) -> dict[str, str] | Malformed | None:
        if not self.files.env_aws.is_file():
            return None
        try:
            return read_env_file(self.files.env_aws)
        except (OSError, UnicodeDecodeError, ValueError):
            return Malformed("deploy/.env.aws cannot be read")

    async def _get(self, url: str) -> Get:
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
    return _step(n, "fail", detail=str(env)) if isinstance(env, Malformed) else None


def _safely_admin(build: Callable[[], AdminBox]) -> AdminBox:
    try:
        return build()
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as e:
        return AdminBox("fail", f"cannot read the evidence ({type(e).__name__})", "unreadable", "")


def banner_safely(f: Files, steps: tuple[Step, ...], tf: TfState | None, admin: AdminBox) -> Banner | None:
    try:
        return banner(f, steps, tf, admin)
    except (OSError, ValueError):
        return None


def account(sts: ProbeResult[str] | None) -> str:
    """The header's account: dashed, or "account unknown: <reason>" (never a bare 12-digit run)."""
    if sts is None:
        return "account unknown: not checked"
    if sts.ok and sts.value and is_account(sts.value):
        return f"account {dashed(sts.value)}"
    return f"account unknown: {sts.reason or 'unexpected answer'}"
