"""Doc 11 §2 "The quiet probes" (T6), §11: each probe with fake binaries first on `PATH` (no AWS, no network):
argv only (no shell of ours), stdin closed, its own process group, the 5 s timeout kills the group, stdout capped
at 256 KB, stderr discarded, output filtered inside the probe, `ProbeResult` never raises, and a missing binary is
a reason ("aws CLI not found (runbook 0.8)", "terraform not found", "terraform not initialised: run plan", "curl
not found: type your address")."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import stat
import time
from pathlib import Path

import pytest
from demo_ui.ops.probes import (
    AWS_MISSING,
    CURL_MISSING,
    MISSING_MARK,
    TF_MISSING,
    TF_UNINITIALISED,
    ProbeExec,
    ProbeResult,
    Probes,
    TfState,
)

pytestmark = pytest.mark.unit

ARN = "arn:aws:iam::123456789012:user/someone"


def fake(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def probes(tmp_path: Path, *, timeout_s: float = 5.0, system: bool = True) -> Probes:
    root = tmp_path / "co"
    root.mkdir(exist_ok=True)
    path = f"{tmp_path / 'bin'}" + (":/usr/bin:/bin" if system else "")
    return Probes(root, {"PATH": path, "AWS_SECRET_ACCESS_KEY": "ui-EXAMPLE-secret"}, timeout_s=timeout_s)


def make_answering(tmp_path: Path, script: str) -> None:
    """A fake `make` whose recipe answer is `script`, chosen by what the --eval recipe asks for."""
    fake(tmp_path / "bin", "make", f'recipe="$5"\n{script}')


def test_sts_ecr_tf_parse_and_filter(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    tf = json.dumps({"region": {"value": "us-east-1", "sensitive": False},
                     "sms_sandbox_commands": {"value": "+16135550101", "sensitive": True}})  # fmt: skip
    make_answering(
        tmp_path,
        f"""echo "{ARN}" >&2
case "$recipe" in
  *sts*) echo 123456789012 ;;
  *ecr*) echo '["att-dev/alerts", "att-dev/tower-mcp"]' ;;
  *terraform*) echo '{tf}' ;;
  *) echo "profile= region=us-east-1 keys=1 cognito=11" ;;
esac""",
    )
    p = probes(tmp_path)
    sts, ecr, tfr, facts = asyncio.run(_all(p))
    assert sts == ProbeResult(True, "123456789012")
    assert ecr == ProbeResult(True, ("att-dev/alerts", "att-dev/tower-mcp"))
    assert tfr == ProbeResult(True, TfState(True, {"region": "us-east-1"}))
    assert isinstance(facts, ProbeResult) and facts.ok and facts.value.cognito == 2  # type: ignore[union-attr]
    assert ARN not in caplog.text and "6135550101" not in caplog.text and "123456789012" not in caplog.text
    assert "probe=sts ok=True" in caplog.text


async def _all(p: Probes) -> tuple[object, ...]:
    return await p.sts(), await p.ecr(), await p.tf(), await p.facts()


def test_unexpected_answers_are_reasons_not_values(tmp_path: Path) -> None:
    make_answering(tmp_path, f'echo "{ARN}"')  # an ARN where an account id was expected
    p = probes(tmp_path)
    sts = asyncio.run(p.sts())
    assert sts == ProbeResult(False, reason="unexpected answer") and ARN not in repr(sts)
    assert asyncio.run(p.ecr()).reason == "unexpected answer"
    assert asyncio.run(p.facts()).reason == "unexpected answer"


def test_missing_binaries_are_reasons(tmp_path: Path) -> None:
    make_answering(
        tmp_path, f"echo {MISSING_MARK}"
    )  # what the recipe's guard prints when its binary is missing
    p = probes(tmp_path)
    assert asyncio.run(p.sts()) == ProbeResult(False, reason=AWS_MISSING)
    assert asyncio.run(p.ecr()) == ProbeResult(False, reason=AWS_MISSING)
    assert asyncio.run(p.tf()) == ProbeResult(False, reason=TF_MISSING)
    make_answering(tmp_path, "exit 2")  # make's own exit code for a failed recipe
    assert asyncio.run(p.tf()) == ProbeResult(False, reason=TF_UNINITIALISED)
    (tmp_path / "co" / "deploy" / "terraform" / ".terraform").mkdir(parents=True)
    assert asyncio.run(p.tf()) == ProbeResult(False, reason="exit 2")
    assert asyncio.run(p.sts()).reason == "exit 2: no credentials?"
    bare = probes(tmp_path, system=False)
    (tmp_path / "bin" / "make").unlink()
    assert asyncio.run(bare.facts()) == ProbeResult(False, reason="make not found")
    assert asyncio.run(bare.checkip()) == ProbeResult(False, reason=CURL_MISSING)


def test_checkip_accepts_one_public_address(tmp_path: Path) -> None:
    for body, expected in (
        ("echo 8.8.8.8", ProbeResult(True, "8.8.8.8/32")),
        ("echo 10.0.0.7", ProbeResult(False, reason="not a public IPv4 /32")),
        ("echo 8.8.8.8/24", ProbeResult(False, reason="not a public IPv4 /32")),
        ("echo '<html>'", ProbeResult(False, reason="not a public IPv4 /32")),
        ("exit 6", ProbeResult(False, reason="exit 6")),
    ):
        fake(tmp_path / "bin", "curl", body)
        assert asyncio.run(probes(tmp_path).checkip()) == expected, body


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except (FileNotFoundError, IndexError):
        return False


def test_timeout_kills_the_group_and_stdin_is_closed(tmp_path: Path) -> None:
    pidfile = tmp_path / "grandchild.pid"
    make_answering(tmp_path, f"sleep 30 & echo $! > {pidfile}; read line; echo got:$line; sleep 30")
    p = probes(tmp_path, timeout_s=0.5)
    t0 = time.monotonic()
    result = asyncio.run(p.sts())
    assert result == ProbeResult(False, reason="timeout (0.5 s)") and time.monotonic() - t0 < 3
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 2
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def test_stdout_is_capped(tmp_path: Path) -> None:
    make_answering(tmp_path, "head -c 300000 /dev/zero | tr '\\0' 'x'")
    assert asyncio.run(probes(tmp_path).sts()) == ProbeResult(False, reason="answer too long")


def test_the_child_env_is_the_scrubbed_base(tmp_path: Path) -> None:
    root = tmp_path / "co"
    root.mkdir()
    ran = asyncio.run(
        ProbeExec(
            root, {"PATH": "/usr/bin:/bin", "AWS_SECRET_ACCESS_KEY": "x-EXAMPLE", "DOCKER_HOST": "d"}
        ).run(("env",), timeout_s=5)
    )
    names = {line.split("=", 1)[0] for line in ran.out.decode().splitlines()}
    assert "AWS_SECRET_ACCESS_KEY" not in names and "DOCKER_HOST" not in names and "PATH" in names


TOOLS = (
    "make",
    "bash",
    "sh",
    "sed",
    "grep",
    "cut",
    "env",
    "cat",
)  # what mk/vars.mk and the recipes need: no aws


@pytest.mark.skipif(any(shutil.which(t) is None for t in TOOLS), reason="needs make and a POSIX userland")
def test_with_real_make_a_missing_aws_cli_is_named_not_an_exit_code(tmp_path: Path) -> None:
    """Real make over a temp checkout (a copy of mk/vars.mk). PATH holds only symlinks to TOOLS, so `aws` and
    `terraform` cannot be found, even on a machine that has them: nothing here can reach AWS."""
    root = tmp_path / "co"
    (root / "mk").mkdir(parents=True)
    shutil.copy(Path(__file__).resolve().parents[3] / "mk" / "vars.mk", root / "mk" / "vars.mk")
    (root / "Makefile").write_text("SHELL := /bin/bash\ninclude mk/vars.mk\n")
    (root / ".env").write_text("AWS_REGION=us-east-1\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    for t in TOOLS:
        (tools / t).symlink_to(shutil.which(t) or t)
    p = Probes(root, {"PATH": str(tools)}, timeout_s=20)
    assert asyncio.run(p.sts()) == ProbeResult(False, reason=AWS_MISSING)
    assert asyncio.run(p.ecr()) == ProbeResult(False, reason=AWS_MISSING)
    assert asyncio.run(p.tf()) == ProbeResult(False, reason=TF_MISSING)
    facts = asyncio.run(p.facts())
    assert facts.ok and facts.value is not None and facts.value.region == "us-east-1"
