"""The Ops runner (doc 11 §2 "The Ops tab", O1, O6, T2–T7): one allowlisted `make` target at a time, on the laptop.

`asyncio.create_subprocess_exec` (never a shell) in its own session, so Stop reaches the whole process group. The
child gets `allowlist.child_env` and the checkout root as `cwd`. Every output line is decoded, stripped of terminal
escapes, passed through `redact.mask_secrets` and `redact.mask`, cut at 2,000 characters, kept in a 500-line backlog
and published every 200 ms as one `ops` frame on the feed's `/events` stream (no second transport). The end of a
job is one `ops-status` frame.

Two runners share the one job slot and the one log (doc 11 §2):
- **local** (`ENV=local`): off with its reason (`off_reason`); Stop is SIGINT, SIGTERM after 1.5 s, SIGKILL at 2 s;
  nothing is written to disk.
- **aws** (`ENV=aws`), only behind the gate (`ops/gate.py`): `request` checks, in the doc's order, gate, busy,
  order (`after`), the typed confirm word (compared, never passed; only then `FORCE=1` on the three whose recipes
  ask) and the `/32` (validated, appended as `ADMIN_CIDR=`). Stop is SIGINT once ("terraform is saving state") and
  SIGTERM on a second press, never SIGKILL (a kill leaves the state lock held). The plan summary of its output
  becomes chips in the `ops-status` frame, and every job is appended to its transcript (T7).

Targets come only from `allowlist.TARGETS`; the constructor's `targets` exists for tests (doc 11 §11), and the
composition root never passes it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from demo_ui import views
from demo_ui.config import read_env_file
from demo_ui.ops.allowlist import DENY, TARGETS, OpsTarget, by_name, child_env
from demo_ui.ops.plan import PlanSummary
from demo_ui.ops.plan import parse as parse_plan
from demo_ui.ops.probes import NOT_A_32, public_slash32
from demo_ui.ops.transcript import Transcript, Transcripts
from demo_ui.redact import mask, mask_secrets

log = logging.getLogger("demo_ui.ops")

JobState = Literal["running", "pass", "fail", "stopped", "confirm"]
# admin-open / admin-close stop at their plan gate (scripts/admin_cidr.py, exit 3 under make) when the plan
# destroys something: that is a pause for the operator, not a failure. The job ends `confirm` (green, no note).
PLAN_GATE = re.compile(r"^admin-(?:open|close): the plan destroys \d+ resource")
# The gate's own chatter stays out of the log: the stop line is the state (green `confirm`), not a message.
GATE_NOISE = (
    PLAN_GATE,
    re.compile(r"^uv run python scripts/admin_cidr\.py plan-gate\b"),
    re.compile(r"^make: \*\*\* \[mk/aws\.mk:\d+: admin-(?:open|close)\] Error 3$"),
)
MAX_LINE = 2_000
MAX_PENDING_BYTES = 65_536  # a line without a newline is flushed at this size
BACKLOG = 500
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class RunnerBusy(RuntimeError):
    """A job (or a macro) is running; the message names it."""


class RunnerOff(RuntimeError):
    """The runner is off here; the message is the doc's reason, shown verbatim."""


class RunRefused(RuntimeError):
    """A `POST /ops/run/{name}` that may not start: the HTTP status and the doc's text."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AwsContext:
    """What admission needs from the stepper (`ops/aws_status.py`): which aws targets' steps are `done`, extra
    per-target blocks (step 6's Cognito passwords) and whether `deploy` needs its word (T5)."""

    done: frozenset[str]
    blocked: Mapping[str, str] = field(default_factory=dict)
    deploy_needs_word: bool = True


STOPPING_AWS = "stopping: terraform is saving state"
U3 = "ENV={env}: the local runner is off; the AWS pipeline is on the right"


def off_reason(*, env: str, container: bool, make_path: str | None, checkout: Path | None) -> str | None:
    """Why "Run here" is greyed (doc 11 §2), or None when every condition holds."""
    if env != "local":
        return U3.format(env=env)
    if container:
        return "runs only in `make showcase-ui` on the host"
    if not make_path:
        return "make not found"
    if checkout is None or not (checkout / "Makefile").is_file() or not (checkout / "mk").is_dir():
        return "checkout not found"
    return None


class FileSecrets:
    """The secret values of an env file (`deploy/compose/.env`: keys matching `allowlist.DENY`), re-read when the
    file changes: `make up` writes it during a job, and the child may print it. Values are kept in memory only."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._stamp: float | None = None
        self._values: tuple[str, ...] = ()

    def __call__(self) -> tuple[str, ...]:
        if self.path is None:
            return ()
        try:
            stamp = self.path.stat().st_mtime
        except OSError:
            self._stamp, self._values = None, ()
            return ()
        if stamp != self._stamp:
            try:
                env = read_env_file(self.path)
            except (OSError, UnicodeDecodeError):
                env = {}
            self._values = tuple(v for k, v in env.items() if DENY.match(k) and v)
            self._stamp = stamp
        return self._values


def clean_line(raw: bytes, secrets: Iterable[str]) -> str:
    """One output line as the browser may see it."""
    text = CONTROL.sub("", ANSI.sub("", raw.decode("utf-8", errors="replace")))
    return mask(mask_secrets(text, secrets))[:MAX_LINE]


@dataclass
class Job:
    target: OpsTarget
    started: float
    argv: tuple[str, ...] = ()
    state: JobState = "running"
    code: int | None = None
    duration_s: float | None = None
    stopping: bool = False
    stops: int = 0
    note: str = ""
    pgid: int | None = None
    plan: PlanSummary | None = None
    transcript: Transcript | None = None
    gated: bool = False  # the plan gate printed its stop line (PLAN_GATE)
    spawned: asyncio.Event = field(default_factory=asyncio.Event)  # set once the child exists (or failed to)

    def view(self) -> dict[str, Any]:
        p = self.plan
        return {
            "name": self.target.name,
            "label": self.target.label,
            "runner": self.target.runner,
            "state": self.state,
            "code": self.code,
            "duration": None if self.duration_s is None else f"{self.duration_s:.1f} s",
            "note": self.note,
            "plan": None if p is None else {"add": p.add, "change": p.change, "destroy": p.destroy},
        }


async def _gate_off() -> str | None:
    return "AWS runs are off"


async def _nothing_done() -> AwsContext:
    return AwsContext(frozenset())


class OpsRunner:
    def __init__(
        self,
        *,
        publish: Callable[[str, str], None],
        cwd: Path | None,
        off: str | None,
        parent_env: Mapping[str, str],
        secrets: Callable[[], Iterable[str]],
        other_busy: Callable[[], bool] = lambda: False,
        targets: Sequence[OpsTarget] = TARGETS,
        gate: Callable[[], Awaitable[str | None]] = _gate_off,
        aws_context: Callable[[], Awaitable[AwsContext]] = _nothing_done,
        transcripts: Transcripts | None = None,
        on_end: Callable[[Job], None] | None = None,
        batch_s: float = 0.2,
        term_after_s: float = 1.5,
        kill_after_s: float = 2.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.publish = publish
        self.on_end = on_end  # after a job's end is published (deps: reload the UI when deploy/.env.aws changed)
        self.cwd = cwd
        self.off = off
        self.parent_env = parent_env
        self.secrets = secrets
        self.other_busy = other_busy
        self.targets = by_name(targets)
        self.gate = gate
        self.aws_context = aws_context
        self.transcripts = transcripts
        self.batch_s = batch_s
        self.term_after_s = term_after_s
        self.kill_after_s = kill_after_s
        self.monotonic = monotonic
        self.job: Job | None = None
        self.task: asyncio.Task[Job] | None = None
        self.backlog: deque[str] = deque(maxlen=BACKLOG)
        self._pending: list[str] = []

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def busy_reason(self) -> str | None:
        if self.busy and self.job is not None:
            return f"running: {self.job.target.name}"
        if self.other_busy():
            return "running: a macro"
        return None

    async def request(
        self, name: str, *, confirm: str | None = None, cidr: str | None = None
    ) -> asyncio.Task[Job]:
        """A `POST /ops/run/{name}` after the origin check: name, runner/gate, busy, order, confirm, cidr (doc 11 §2
        "Routes"), each refusal a `RunRefused` with its status; else the job starts."""
        target = self.targets.get(name)
        if target is None:
            raise RunRefused(404, "Ops: not in the allowlist")
        off = self.off if target.runner == "local" else await self.gate()
        if off:
            raise RunRefused(403, off)
        busy = self.busy_reason()
        if busy:
            raise RunRefused(409, busy)
        extra: tuple[str, ...] = ()
        if target.runner == "aws":
            extra = await self._admit_aws(target, confirm, cidr)
        try:
            return self.start(name, extra)
        except RunnerBusy as e:  # another request started while this one waited for the gate or the stepper
            raise RunRefused(409, str(e)) from None

    async def _admit_aws(self, target: OpsTarget, confirm: str | None, cidr: str | None) -> tuple[str, ...]:
        ctx = await self.aws_context()
        for after in target.after:
            if after not in ctx.done:
                raise RunRefused(403, f"after {self.targets[after].label}")
        if target.name in ctx.blocked:
            raise RunRefused(403, ctx.blocked[target.name])
        extra: tuple[str, ...] = ()
        word = target.confirm
        if word and (target.confirm_when == "always" or ctx.deploy_needs_word):
            if (confirm or "").strip() != word:  # compared, never passed to the child
                raise RunRefused(400, f"type {word} to confirm")
            if target.forces:
                extra += ("FORCE=1",)
        if target.takes_cidr:
            normalised = public_slash32(cidr or "")
            if normalised is None:
                raise RunRefused(400, NOT_A_32)
            extra += (f"ADMIN_CIDR={normalised}",)
        return extra

    def start(self, name: str, extra: tuple[str, ...] = ()) -> asyncio.Task[Job]:
        """Launch an admitted target: `KeyError` for a name not in the allowlist, `RunnerOff` (local runner off), or
        `RunnerBusy` (a job or a macro). The aws runner's checks are `request`'s; `extra` comes only from there."""
        target = self.targets[name]
        if target.runner == "local" and self.off:
            raise RunnerOff(self.off)
        busy = self.busy_reason()
        if busy:
            raise RunnerBusy(busy)
        self.job = Job(target, self.monotonic(), argv=(*target.argv, *extra))
        if target.runner == "aws" and self.transcripts is not None:
            self.job.transcript = self.transcripts.open(name)
            self.job.transcript.start(name, self.job.argv)
        self.backlog.clear()
        self._pending.clear()
        self._status()
        self.task = asyncio.create_task(self._run(self.job), name=f"ops:{name}")
        return self.task

    async def stop(self) -> bool:
        """Local: SIGINT to the group, SIGTERM after `term_after_s`, SIGKILL at `kill_after_s`. Aws: SIGINT on the
        first press, SIGTERM on the second, never SIGKILL. False if nothing runs."""
        job, task = self.job, self.task
        if job is None or task is None or task.done():
            return False
        job.stopping = True
        await job.spawned.wait()
        if job.pgid is None:  # it never started
            await task
            return True
        if job.target.runner == "aws":
            job.stops += 1
            _signal_group(job.pgid, signal.SIGINT if job.stops == 1 else signal.SIGTERM)
            job.note = STOPPING_AWS
            self._status()
            return True
        t0 = self.monotonic()
        steps = (
            (signal.SIGINT, 0.0),
            (signal.SIGTERM, self.term_after_s),
            (signal.SIGKILL, self.kill_after_s),
        )
        for sig, at in steps:
            while self.monotonic() - t0 < at:
                if task.done():
                    return True
                await asyncio.sleep(0.05)
            _signal_group(job.pgid, sig)
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
        return True

    async def aclose(self) -> None:
        """App shutdown: a job never outlives the UI process (an aws job gets SIGINT: terraform saves its state)."""
        if self.busy and self.job is not None and self.job.pgid is not None:
            _signal_group(self.job.pgid, signal.SIGINT if self.job.target.runner == "aws" else signal.SIGKILL)
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    def view(self) -> dict[str, Any]:
        """The "Run here" column's state: the last job and its backlog (a page that opens mid-job)."""
        return {"job": self.job.view() if self.job else None, "lines": list(self.backlog), "busy": self.busy}

    # --- the job --------------------------------------------------------------------------------------------

    async def _run(self, job: Job) -> Job:
        log.info("ops start target=%s", job.target.name)
        flusher = asyncio.create_task(self._flush_every())
        try:
            code = await self._exec(job)
        finally:
            flusher.cancel()
        self._flush()
        job.code = code
        job.duration_s = self.monotonic() - job.started
        job.state = "stopped" if job.stopping else ("pass" if code == 0 else "fail")
        job.note = ""
        if job.state == "fail" and job.gated:
            job.state = "confirm"  # shown green like a pass; the plan chips say what `deploy` will do
        if job.transcript is not None:
            if job.plan is not None:
                job.transcript.plan(job.plan)
            job.transcript.end(job.state, code, job.duration_s)
        self._status()
        log.info("ops end target=%s state=%s code=%s", job.target.name, job.state, code)
        if self.on_end is not None:
            self.on_end(job)
        return job

    async def _exec(self, job: Job) -> int | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *job.argv,
                cwd=self.cwd,
                env=child_env(job.target, self.parent_env),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            job.pgid = proc.pid  # start_new_session: the child leads its own group
        except OSError as e:
            self._line(f"could not start: {type(e).__name__}".encode())
            return None
        finally:
            job.spawned.set()
        reader = asyncio.create_task(self._read(proc))
        code = await proc.wait()
        try:
            await asyncio.wait_for(reader, timeout=1.0)
        except TimeoutError:  # a grandchild still holds the pipe after the child exited: the job is over
            _signal_group(job.pgid, signal.SIGTERM if job.target.runner == "aws" else signal.SIGKILL)
            reader.cancel()
        return code

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stdout is None:
            return
        partial = b""
        while chunk := await proc.stdout.read(4096):
            parts = re.split(rb"\r\n|\n|\r", partial + chunk)
            partial = parts.pop()
            for raw in parts:
                self._line(raw)
            if len(partial) > MAX_PENDING_BYTES:
                self._line(partial)
                partial = b""
        if partial:
            self._line(partial)

    def _line(self, raw: bytes) -> None:
        line = clean_line(raw, self.secrets())
        job = self.job
        if job is not None and job.target.runner == "aws":
            job.plan = parse_plan(line) or job.plan
            if PLAN_GATE.match(line):
                job.gated = True
            if any(p.match(line) for p in GATE_NOISE):
                return  # neither shown nor written to the transcript
        self.backlog.append(line)
        self._pending.append(line)

    async def _flush_every(self) -> None:
        while True:
            await asyncio.sleep(self.batch_s)
            self._flush()

    def _flush(self) -> None:
        if self._pending:
            batch, self._pending = self._pending, []
            if self.job is not None and self.job.transcript is not None:
                self.job.transcript.write(batch)  # exactly as the browser gets them: masked, then cut
            self.publish("ops", views.render("ops_log.html", lines=batch))

    def _status(self) -> None:
        if self.job is not None:
            self.publish("ops-status", views.render("ops_status.html", job=self.job.view()))


def _signal_group(pgid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)
