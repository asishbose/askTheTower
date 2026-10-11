"""Doc 11 §2 "Pane 5", §11: the Ops runner with real subprocesses. The runner takes its targets from the constructor
here (`sh -c …`, `make --version`); production code builds it from `allowlist.TARGETS` only.

Lines arrive in order as `ops` frames on the feed (the `/events` fan-out), masked; Stop ends a process group, child
and grandchild, within 2 s; one job at a time (also against a macro); exit codes become the badge; the child never
sees the parent's credentials.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path

import pytest
from demo_ui.feed import Feed, Frame
from demo_ui.ops.allowlist import OpsTarget
from demo_ui.ops.runner import OpsRunner, RunnerBusy, RunnerOff, off_reason

from tests.privacy.patterns import E164_STRICT, phone_hits

pytestmark = pytest.mark.integration

KNOWN = "known-secret-0f9e8d7c"
PLANTED = (
    "echo one; echo 'call +16135550101 or (613) 555-0102'; echo 'Authorization: Bearer eyJhbGciOi.payload'; "
    f"echo 'AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG'; echo 'MOCK_ADMIN_TOKEN=abcdef123456'; echo {KNOWN}; "
    "echo AKIAIOSFODNN7EXAMPLE; printf '\\033[1;32mgreen\\033[0m\\n'; echo last"
)


def sh(name: str, script: str) -> OpsTarget:
    return OpsTarget(name, ("sh", "-c", script), "tests", name, ("base",), needs_stack=False)


TARGETS = (
    sh("planted", PLANTED),
    sh("sleeper", "sleep 30 & echo grandchild=$!; echo 'Bearer tok-sleeping-1234'; sleep 30"),
    sh("exit3", "echo bye; exit 3"),
    sh("env", "env"),
    OpsTarget("make-version", ("make", "--version"), "tests", "make", ("base",), needs_stack=False),
)


def runner(
    feed: Feed, *, macro_busy: bool = False, off: str | None = None, parent: dict[str, str] | None = None
) -> OpsRunner:
    return OpsRunner(
        publish=feed.publish,
        cwd=Path.cwd(),
        off=off,
        parent_env=parent if parent is not None else dict(os.environ),
        secrets=lambda: [KNOWN],
        other_busy=lambda: macro_busy,
        targets=TARGETS,
        batch_s=0.05,
    )


def drain(q: asyncio.Queue[Frame]) -> list[Frame]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def lines_of(frames: list[Frame]) -> list[str]:
    return [
        m for f in frames if f.event == "ops" for m in re.findall(r'<span class="ln">(.*?)</span>', f.html)
    ]


async def test_lines_arrive_in_order_masked_as_ops_frames_and_a_pass_badge() -> None:
    feed = Feed([], 1.0)
    q = feed.subscribe()
    r = runner(feed)
    job = await r.start("planted")
    frames = drain(q)
    lines = lines_of(frames)
    assert lines[0] == "one" and lines[-1] == "last" and "green" in lines
    joined = "\n".join(f.html for f in frames)
    assert phone_hits(joined) == [] and E164_STRICT.findall(joined) == []
    for secret in (
        "eyJhbGciOi",
        "wJalrXUtnFEMI",
        "abcdef123456",
        KNOWN,
        "AKIAIOSFODNN7EXAMPLE",
        "555-0102",
        "\x1b",
    ):
        assert secret not in joined, secret
    statuses = [f for f in frames if f.event == "ops-status"]
    assert [re.search(r'data-state="(\w+)"', f.html)[1] for f in statuses] == ["running", "pass"]  # type: ignore[index]
    assert job.state == "pass" and job.code == 0 and "exit 0" in statuses[-1].html
    assert r.view()["lines"] == lines and not r.busy


async def test_exit_code_three_is_a_fail_badge_with_its_code() -> None:
    feed = Feed([], 1.0)
    q = feed.subscribe()
    job = await runner(feed).start("exit3")
    assert job.state == "fail" and job.code == 3
    last = [f for f in drain(q) if f.event == "ops-status"][-1].html
    assert 'data-state="fail"' in last and "exit 3" in last and " s" in last


def _alive(pid: int) -> bool:
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text().split(") ", 1)[1][0] != "Z"
    except (FileNotFoundError, IndexError):
        return False


async def test_stop_ends_the_group_child_and_grandchild_within_two_seconds() -> None:
    feed = Feed([], 1.0)
    q = feed.subscribe()
    r = runner(feed)
    task = r.start("sleeper")
    deadline = time.monotonic() + 10
    while not any(line.startswith("grandchild=") for line in r.backlog):
        assert time.monotonic() < deadline, list(r.backlog)
        await asyncio.sleep(0.05)
    grandchild = int(next(line for line in r.backlog if line.startswith("grandchild=")).split("=")[1])
    assert _alive(grandchild)
    t0 = time.monotonic()
    assert await r.stop() is True
    job = await task
    assert time.monotonic() - t0 < 2.0 + 0.5  # SIGKILL at 2 s at the latest, plus scheduling
    assert job.state == "stopped" and job.code is not None and job.code != 0
    deadline = time.monotonic() + 2
    while _alive(grandchild) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert not _alive(grandchild)
    joined = "\n".join(f.html for f in drain(q))
    assert "tok-sleeping-1234" not in joined and 'data-state="stopped"' in joined
    assert await r.stop() is False  # nothing runs now


async def test_one_job_at_a_time_and_never_beside_a_macro() -> None:
    feed = Feed([], 1.0)
    r = runner(feed)
    task = r.start("sleeper")
    with pytest.raises(RunnerBusy, match="running: sleeper"):
        r.start("exit3")
    t0 = time.monotonic()
    assert await r.stop() is True  # pressed before the child even exists: it still stops it
    assert (await task).state == "stopped" and time.monotonic() - t0 < 2.5
    with pytest.raises(RunnerBusy, match="macro"):
        runner(feed, macro_busy=True).start("exit3")


async def test_unknown_target_and_runner_off() -> None:
    feed = Feed([], 1.0)
    with pytest.raises(KeyError):
        runner(feed).start("rm -rf /")
    with pytest.raises(RunnerOff, match="ENV=aws"):
        runner(feed, off="ENV=aws: nothing runs here; use the commands on the right in a terminal").start(
            "exit3"
        )


async def test_the_child_never_sees_the_parents_credentials() -> None:
    parent = {
        **{k: v for k, v in os.environ.items() if k in ("PATH", "HOME")},
        "AWS_PROFILE": "att", "AWS_ACCESS_KEY_ID": "AKIAPLANTEDPLANTED12", "AWS_SECRET_ACCESS_KEY": "planted-secret",
        "MOCK_ADMIN_TOKEN": "planted-token", "TF_VAR_x": "1", "ENV": "aws", "TOWER_BEARER": "planted-bearer",
    }  # fmt: skip
    r = runner(Feed([], 1.0), parent=parent)
    await r.start("env")
    names = {line.split("=", 1)[0] for line in r.backlog if "=" in line}
    assert "PATH" in names and "NO_COLOR" in names
    assert names & {"AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "MOCK_ADMIN_TOKEN", "TF_VAR_x",
                    "ENV", "TOWER_BEARER"} == set()  # fmt: skip
    assert "planted" not in "\n".join(r.backlog)


@pytest.mark.skipif(shutil.which("make") is None, reason="make not on PATH")
async def test_make_version_runs_from_the_checkout() -> None:
    r = runner(Feed([], 1.0))
    job = await r.start("make-version")
    assert job.state == "pass" and any("GNU Make" in line for line in r.backlog)


def test_off_reasons_are_the_docs_words(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    (root / "mk").mkdir(parents=True)
    (root / "Makefile").write_text("all:\n")
    ok = {"env": "local", "container": False, "make_path": "/usr/bin/make", "checkout": root}
    assert off_reason(**ok) is None  # type: ignore[arg-type]
    assert off_reason(**{**ok, "env": "aws"}) == (  # type: ignore[arg-type]
        "ENV=aws: nothing runs here; use the commands on the right in a terminal"
    )
    assert off_reason(**{**ok, "container": True}) == "runs only in `make showcase-ui` on the host"  # type: ignore[arg-type]
    assert off_reason(**{**ok, "make_path": None}) == "make not found"  # type: ignore[arg-type]
    assert off_reason(**{**ok, "checkout": tmp_path}) == "checkout not found"  # type: ignore[arg-type]
    assert off_reason(**{**ok, "checkout": None}) == "checkout not found"  # type: ignore[arg-type]
