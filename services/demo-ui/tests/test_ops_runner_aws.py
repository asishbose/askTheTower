"""Doc 11 §2 "The Ops tab" (T2–T7), §11: the aws runner over ASGI, composed by `build_deps`, with a fake `make`
first on `PATH` (it echoes its argv and env keys, prints scripted output and exits as told; it also answers the
gate's config-check and the quiet probes, and a fake `curl` answers checkip). No real make target, no AWS call, no
network. Covered: confirm words (missing, wrong, other case → 400 and the fake never ran; right → 202 with FORCE=1
on the three whose recipes ask, never on deploy); plan chips and the transcript's plan line; deploy's word when the
last plan destroys; a stale plan → 403 "after 3 · …"; the `/32` rule; transcript files (0600 in 0700, appended,
start/plan/end lines, masked); a local job writes none; Stop is SIGINT then SIGTERM, never SIGKILL; gate off → 403."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import stat
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from demo_ui.app import create_app
from demo_ui.config import Settings
from demo_ui.deps import Deps, build_deps
from demo_ui.feed import Frame
from tower_consent import Store

from tests.privacy.patterns import AWS_KEY_ID, BEARER, phone_hits

from .fakes import Recorder

pytestmark = pytest.mark.integration

HX = {"HX-Request": "true", "Origin": "http://ui.test"}
SECRET_40 = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
SESSION = "FwoGZXIvYXdzEXAMPLE" + "Zm9vYmFy" * 16
PLANTED_OUT = (
    "echo 'Plan: 2 to add, 1 to change, 3 to destroy.'; echo 'id AKIAIOSFODNN7EXAMPLE'; "
    f"echo 'aws_secret_access_key = {SECRET_40}'; echo 'bare {SECRET_40} end'; echo 'token {SESSION}'; "
    "echo 'call +16135550101'; echo 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.e30.EXAMPLEsignature'"
)
FAKE_MAKE = f"""#!/bin/bash
last="${{@: -1}}"
if [ "$last" = "__ops_probe" ]; then
  case "$5" in
    *COGNITO*) echo "profile= region=us-east-1 keys=1 cognito=11" ;;
    *) echo __ops_missing__ ;;
  esac
  exit 0
fi
case " $* " in *" config-check "*) exit 0 ;; esac
echo "$*" >> "$PWD/ran.txt"
echo "argv: $*"
echo "envkeys: $(env | cut -d= -f1 | sort | tr '\\n' ' ')"
case "$1" in
  plan) {PLANTED_OUT} ;;
  down)
    if [ -f "$PWD/hold" ]; then
      trap 'echo got-int' INT
      trap 'echo got-term; exit 143' TERM
      echo ready
      while :; do sleep 0.1; done
    fi ;;
esac
echo done
exit 0
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@dataclass
class Rig:
    c: httpx.AsyncClient
    deps: Deps
    root: Path

    def ran(self) -> list[str]:
        f = self.root / "ran.txt"
        return f.read_text().splitlines() if f.exists() else []

    def write(self, rel: str, text: str, at: float | None = None) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if at is not None:
            os.utime(p, (at, at))

    async def run(self, name: str, **form: str) -> httpx.Response:
        return await self.c.post(f"/ops/run/{name}", headers=HX, data=form)

    async def finish(self) -> None:
        assert self.deps.ops.task is not None
        await asyncio.wait_for(self.deps.ops.task, 20)

    def logs(self) -> list[Path]:
        d = self.root / "artifacts" / "ops-log"
        return sorted(d.glob("*.log")) if d.exists() else []


def _rig_deps(store: Store, root: Path, bin_dir: Path, aws_ops: str) -> Deps:
    rec = Recorder()
    path = f"{bin_dir}:/usr/bin:/bin"
    assert shutil.which("make", path=path) == str(bin_dir / "make")  # the fake, never the real one
    environ = {
        "PATH": path,
        "HOME": str(root),
        "AWS_SECRET_ACCESS_KEY": "ui-EXAMPLE-parent-secret",
        "ENV": "aws",
    }
    return build_deps(
        Settings(checkout=root, aws_ops=aws_ops, mock_admin_token="mt", alerts_bearer="ab"),
        transports={
            "mock": rec.mock(),
            "binding": rec.binding(),
            "alerts": rec.alerts(),
            "ops": httpx.MockTransport(lambda _: httpx.Response(200)),
        },  # fmt: skip
        store=store,
        reset=None,
        mode="scripted",
        environ=environ,
        which=lambda name: shutil.which(name, path=path),
    )


@pytest.fixture
async def rig(store: Store, tmp_path: Path) -> AsyncIterator[Rig]:
    root = tmp_path / "checkout"
    (root / "mk").mkdir(parents=True)
    (root / "Makefile").write_text("all:\n")
    (root / ".env").write_text("AWS_REGION=us-east-1\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "make", FAKE_MAKE)
    _exe(bin_dir / "curl", "#!/bin/sh\necho 8.8.8.8\n")
    deps = _rig_deps(store, root, bin_dir, "1")
    app = create_app(deps)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        yield Rig(c, deps, root)
    await deps.ops.aclose()


def _frames(q: asyncio.Queue[Frame]) -> list[Frame]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _clean(text: str) -> list[str]:
    hits = [*phone_hits(text), *(m.group(0) for m in AWS_KEY_ID.finditer(text)), *BEARER.findall(text)]
    return hits + [s for s in (SECRET_40[:12], SESSION[:20], "ui-EXAMPLE-parent-secret") if s in text]


async def test_the_destructive_words(rig: Rig) -> None:
    for name, word in (("aws-down", "down"), ("aws-down-all", "down-all"), ("aws-admin-close", "close")):
        before, task = len(rig.ran()), rig.deps.ops.task
        for given in (None, "nope", word.upper(), f"{word}!"):
            r = await rig.run(name, **({"confirm": given} if given is not None else {}))
            assert (r.status_code, f"type {word} to confirm" in r.text) == (400, True), (name, given)
        assert len(rig.ran()) == before and rig.deps.ops.task is task  # nothing ran
        r = await rig.run(name, confirm=f"  {word} ")  # surrounding whitespace is stripped
        assert r.status_code == 202, r.text
        await rig.finish()
        argv = rig.ran()[-1].split()
        assert argv == [f"{rig.deps.ops.targets[name].make_target}", "ENV=aws", "FORCE=1"], argv
    log = "\n".join(rig.deps.ops.backlog)
    assert (
        "envkeys:" in log and "AWS_SECRET_ACCESS_KEY" not in log.split("envkeys:")[1]
    )  # the parent's never pass


async def test_plan_chips_transcript_and_deploys_word(rig: Rig) -> None:
    q = rig.deps.feed.subscribe()
    assert (await rig.run("aws-plan")).status_code == 403  # order: no push yet
    rig.write("artifacts/tf-outputs-ecr.json",
              json.dumps({"ecr_repositories": {s: "r" for s in
                          ("mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client")}}), 1_000)  # fmt: skip
    r = await rig.run("aws-plan")
    assert r.status_code == 403 and "after 2 · Push the five images (linux/arm64)" in r.text
    rig.write("artifacts/image-tag", "abc1234\n", 2_000)
    for _ in range(2):  # a second run appends to the same file
        assert (await rig.run("aws-plan")).status_code == 202
        await rig.finish()
    frames = _frames(q)
    last = [f for f in frames if f.event == "ops-status"][-1].html
    assert 'data-plan="+2 ~1 -3"' in last and "pchip destroy hot" in last and 'data-state="pass"' in last
    for f in frames:
        assert _clean(f.html) == [], f.html

    [log] = rig.logs()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-aws-plan\.log", log.name)
    assert stat.S_IMODE(log.stat().st_mode) == 0o600 and stat.S_IMODE(log.parent.stat().st_mode) == 0o700
    text = log.read_text()
    assert text.count(" start aws-plan: make plan ENV=aws") == 2 and text.count("== plan +2 ~1 -3") == 2
    assert len(re.findall(r"^== \S+ end pass exit 0 [\d.]+ s$", text, re.M)) == 2
    assert _clean(text) == [] and "aws_secret_access_key=••••" in text

    # The plan file `make plan` writes: destroys → deploy needs its word, without FORCE=1.
    rig.write("artifacts/terraform-plan.txt", "Plan: 2 to add, 1 to change, 3 to destroy.\n", 3_000)
    assert (await rig.run("aws-deploy")).text.count("type deploy to confirm") == 1
    assert (await rig.run("aws-deploy", confirm="Deploy")).status_code == 400
    assert (await rig.run("aws-deploy", confirm="deploy")).status_code == 202
    await rig.finish()
    assert rig.ran()[-1] == "deploy ENV=aws"
    rig.write("artifacts/terraform-plan.txt", "No changes.\n", 3_000)
    assert (await rig.run("aws-deploy")).status_code == 202  # nothing destroyed: no word
    await rig.finish()
    html = (await rig.c.get("/fragment/ops-checklist")).text
    assert 'placeholder="type deploy"' not in html
    rig.write("artifacts/terraform-plan.txt", "no summary here\n", 3_000)
    html = (await rig.c.get("/fragment/ops-checklist")).text
    assert 'placeholder="type deploy"' in html  # unreadable counts as destroying
    rig.write("artifacts/terraform-plan.txt", "No changes.\n", 1_500)  # older than image-tag
    r = await rig.run("aws-deploy", confirm="deploy")
    assert r.status_code == 403 and "after 3 · terraform plan (read it)" in r.text


async def test_admin_open_takes_only_a_public_slash32(rig: Rig) -> None:
    r = await rig.run("aws-admin-open", cidr="8.8.8.8")
    assert r.status_code == 403 and "after 4 · Deploy" in r.text  # nothing in state yet
    rig.write(
        "artifacts/tf-outputs.json",
        json.dumps({"binding_url": {"value": "https://b.example", "sensitive": False}}),
    )
    rig.write("deploy/.env.aws", "TOWER_URL=https://t.example/mcp\nBINDING_URL=https://b.example\n")
    for bad in ("10.0.0.1/32", "127.0.0.1", "203.0.113.7", "8.8.8.0/24", "::1", "1.2.3.4; rm -rf /", ""):
        r = await rig.run("aws-admin-open", cidr=bad)
        assert (r.status_code, "not a public IPv4 /32" in r.text) == (400, True), bad
    assert rig.ran() == []
    r = await rig.run("aws-admin-open", cidr="8.8.8.8", extra="ignored")
    assert r.status_code == 202, r.text
    await rig.finish()
    assert rig.ran()[-1] == "admin-open ENV=aws ADMIN_CIDR=8.8.8.8/32"
    [log] = [p for p in rig.logs() if p.name.endswith("-aws-admin-open.log")]
    first = log.read_text().splitlines()[0]
    assert first.endswith("start aws-admin-open: make admin-open ENV=aws ADMIN_CIDR=<cidr>"), first
    html = (await rig.c.get("/fragment/ops-checklist")).text
    assert 'value="8.8.8.8/32"' in html  # the checkip default, only in its own field


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except (FileNotFoundError, IndexError):
        return False


async def test_stop_on_an_aws_job_is_sigint_then_sigterm_never_sigkill(rig: Rig) -> None:
    (rig.root / "hold").write_text("")
    q = rig.deps.feed.subscribe()
    assert (await rig.run("aws-down", confirm="down")).status_code == 202
    ops = rig.deps.ops
    deadline = time.monotonic() + 10
    while "ready" not in ops.backlog:
        assert time.monotonic() < deadline, list(ops.backlog)
        await asyncio.sleep(0.05)
    pgid = ops.job.pgid  # type: ignore[union-attr]
    assert pgid is not None
    assert (await rig.c.post("/ops/stop", headers=HX)).status_code == 200
    await asyncio.sleep(2.5)  # past local's SIGKILL point: an aws job is still saving state
    assert _alive(pgid) and ops.busy and "got-int" in ops.backlog
    status = [f for f in _frames(q) if f.event == "ops-status"][-1].html
    assert "stopping: terraform is saving state" in status
    assert (await rig.c.post("/ops/stop", headers=HX)).status_code == 200
    job = await asyncio.wait_for(ops.task, 5)  # type: ignore[arg-type]
    assert job.state == "stopped" and job.code == 143 and "got-term" in ops.backlog
    text = [p for p in rig.logs() if p.name.endswith("-aws-down.log")][0].read_text()
    assert re.search(r"end stopped exit 143 ", text)


async def test_gate_off_runs_nothing_and_a_local_job_writes_no_transcript(
    store: Store, tmp_path: Path
) -> None:
    root = tmp_path / "checkout"
    (root / "mk").mkdir(parents=True)
    (root / "Makefile").write_text("all:\n")
    (root / ".env").write_text("A=1\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "make", FAKE_MAKE)
    deps = _rig_deps(store, root, bin_dir, "")
    app = create_app(deps)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ui.test") as c:
        rig = Rig(c, deps, root)
        r = await rig.run("aws-ecr-up")
        assert r.status_code == 403 and "AWS runs are off: start the UI with make ops-ui" in r.text
        assert rig.ran() == []
        header = (await c.get("/fragment/ops-header")).text
        assert "AWS runs: off: AWS runs are off" in header and "account" not in header
        html = (await c.get("/fragment/ops-checklist")).text
        assert html.count("disabled") >= 14 and "Nothing deployed yet" in html
        assert (await c.get("/healthz")).json()["ops"] == {"runner": True, "aws": "off", "running": False}
        r = await rig.run("up")  # the local runner: ENV=local, scrubbed, no transcript
        assert r.status_code == 202
        await rig.finish()
        assert rig.ran() == ["up ENV=local"] and rig.logs() == []
    await deps.ops.aclose()
