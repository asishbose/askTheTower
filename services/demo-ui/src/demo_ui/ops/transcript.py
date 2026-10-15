"""Transcripts of aws-runner jobs (doc 11 §2 "Transcripts", T7): `artifacts/ops-log/<YYYY-MM-DD>-<name>.log`.

Directory 0700, file 0600, appended (gitignored by `artifacts/**`). A start line with the argv (`ADMIN_CIDR` shown as
`<cidr>`), every line exactly as it went to the browser (masked, then cut: the runner hands over cleaned lines only),
a plan line when a summary was seen, and an end line. `last(name)` reads the newest file's last end line: the
evidence of steps that leave no file of their own. A write that fails is logged by name and never stops the job.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from demo_ui.ops.plan import PlanSummary

log = logging.getLogger("demo_ui.ops.transcript")

DIR_MODE = 0o700
FILE_MODE = 0o600
REL = Path("artifacts") / "ops-log"
END = re.compile(r"^== (?P<at>\S+) end (?P<state>pass|fail|stopped|confirm) exit (?P<code>\S+) (?P<secs>[\d.]+) s$")
FILE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<name>[a-z0-9-]+)\.log$")
CIDR_ARG = re.compile(r"^ADMIN_CIDR=.*$")


def iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def shown_argv(argv: Iterable[str]) -> str:
    """The argv as the start line shows it: the `/32` is never written (doc 11 §2 "Admin path /32")."""
    return " ".join("ADMIN_CIDR=<cidr>" if CIDR_ARG.match(a) else a for a in argv)


@dataclass(frozen=True)
class LastRun:
    state: str
    at: datetime


class Transcript:
    """One job's appends to one file."""

    def __init__(self, path: Path, wall: Callable[[], datetime]) -> None:
        self.path = path
        self.wall = wall
        self._broken = False

    def write(self, lines: Iterable[str]) -> None:
        text = "".join(f"{line}\n" for line in lines)
        if not text or self._broken:
            return
        try:
            self.path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
            os.chmod(self.path.parent, DIR_MODE)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE)
            try:
                os.fchmod(fd, FILE_MODE)
                os.write(fd, text.encode("utf-8"))
            finally:
                os.close(fd)
        except OSError as e:
            self._broken = True
            log.warning("ops transcript write failed file=%s error=%s", self.path.name, type(e).__name__)

    def start(self, name: str, argv: Iterable[str]) -> None:
        self.write([f"== {iso(self.wall())} start {name}: {shown_argv(argv)}"])

    def plan(self, summary: PlanSummary) -> None:
        self.write([summary.line])

    def end(self, state: str, code: int | None, seconds: float) -> None:
        self.write(
            [f"== {iso(self.wall())} end {state} exit {'-' if code is None else code} {seconds:.1f} s"]
        )


class Transcripts:
    def __init__(self, directory: Path, wall: Callable[[], datetime]) -> None:
        self.directory = directory
        self.wall = wall

    def open(self, name: str) -> Transcript:
        return Transcript(self.directory / f"{self.wall().astimezone(UTC):%Y-%m-%d}-{name}.log", self.wall)

    def last(self, name: str) -> LastRun | None:
        """The newest end line of `name`'s transcripts, or None (no file, or no job of it ended)."""
        try:
            files = sorted(
                (p for p in self.directory.iterdir() if (m := FILE.match(p.name)) and m["name"] == name),
                reverse=True,
            )
        except OSError:
            return None
        for path in files:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                m = END.match(line)
                if m:
                    try:
                        at = datetime.strptime(m["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                    except ValueError:
                        break
                    return LastRun(m["state"], at)
        return None
