"""The Ops runner (doc 11 §2 "Pane 5", O1, O2, O6): one allowlisted `make` target at a time, on the laptop.

`asyncio.create_subprocess_exec` (never a shell) in its own session, so Stop reaches the whole process group:
SIGINT, then SIGTERM after 1.5 s, then SIGKILL at 2 s. The child gets `allowlist.child_env` and the checkout root as
`cwd`. Every output line is decoded, stripped of terminal escapes, passed through `redact.mask_secrets` and
`redact.mask`, cut at 2,000 characters, kept in a 500-line backlog and published every 200 ms as one `ops` frame
on the feed's `/events` stream (no second transport). The end of a job is one `ops-status` frame. Nothing is written
to disk.

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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from demo_ui import views
from demo_ui.config import read_env_file
from demo_ui.ops.allowlist import DENY, TARGETS, OpsTarget, by_name, child_env
from demo_ui.redact import mask, mask_secrets

log = logging.getLogger("demo_ui.ops")

JobState = Literal["running", "pass", "fail", "stopped"]
MAX_LINE = 2_000
MAX_PENDING_BYTES = 65_536  # a line without a newline is flushed at this size
BACKLOG = 500
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class RunnerBusy(RuntimeError):
    """A job (or a macro) is running; the message names it."""


class RunnerOff(RuntimeError):
    """The runner is off here; the message is the doc's reason, shown verbatim."""


def off_reason(*, env: str, container: bool, make_path: str | None, checkout: Path | None) -> str | None:
    """Why "Run here" is greyed (doc 11 §2), or None when every condition holds."""
    if env != "local":
        return f"ENV={env}: nothing runs here; use the commands on the right in a terminal"
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
    state: JobState = "running"
    code: int | None = None
    duration_s: float | None = None
    stopping: bool = False
    pgid: int | None = None
    spawned: asyncio.Event = field(default_factory=asyncio.Event)  # set once the child exists (or failed to)

    def view(self) -> dict[str, Any]:
        return {
            "name": self.target.name,
            "label": self.target.label,
            "state": self.state,
            "code": self.code,
            "duration": None if self.duration_s is None else f"{self.duration_s:.1f} s",
        }


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
        batch_s: float = 0.2,
        term_after_s: float = 1.5,
        kill_after_s: float = 2.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.publish = publish
        self.cwd = cwd
        self.off = off
        self.parent_env = parent_env
        self.secrets = secrets
        self.other_busy = other_busy
        self.targets = by_name(targets)
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

    def start(self, name: str) -> asyncio.Task[Job]:
        """`KeyError` for a name not in the allowlist, `RunnerOff`, or `RunnerBusy` (a job or a macro)."""
        target = self.targets[name]
        if self.off:
            raise RunnerOff(self.off)
        if self.busy and self.job is not None:
            raise RunnerBusy(f"running: {self.job.target.name}")
        if self.other_busy():
            raise RunnerBusy("running: a macro")
        self.job = Job(target, self.monotonic())
        self.backlog.clear()
        self._pending.clear()
        self._status()
        self.task = asyncio.create_task(self._run(self.job), name=f"ops:{name}")
        return self.task

    async def stop(self) -> bool:
        """SIGINT to the group, SIGTERM after `term_after_s`, SIGKILL at `kill_after_s`. False if nothing runs."""
        job, task = self.job, self.task
        if job is None or task is None or task.done():
            return False
        job.stopping = True
        await job.spawned.wait()
        if job.pgid is None:  # it never started
            await task
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
        """App shutdown: a job never outlives the UI process."""
        if self.busy and self.job is not None and self.job.pgid is not None:
            _signal_group(self.job.pgid, signal.SIGKILL)
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
        self._status()
        log.info("ops end target=%s state=%s code=%s", job.target.name, job.state, code)
        return job

    async def _exec(self, job: Job) -> int | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *job.target.argv,
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
            _signal_group(job.pgid, signal.SIGKILL)
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
        self.backlog.append(line)
        self._pending.append(line)

    async def _flush_every(self) -> None:
        while True:
            await asyncio.sleep(self.batch_s)
            self._flush()

    def _flush(self) -> None:
        if self._pending:
            batch, self._pending = self._pending, []
            self.publish("ops", views.render("ops_log.html", lines=batch))

    def _status(self) -> None:
        if self.job is not None:
            self.publish("ops-status", views.render("ops_status.html", job=self.job.view()))


def _signal_group(pgid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)
