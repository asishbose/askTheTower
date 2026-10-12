"""Doc 11 §11 "Pane 5 … Privacy": the ops stream sweep. One job prints planted E.164 numbers (grouped and bare),
`Bearer` values, `*_TOKEN=`/`AWS_SECRET_ACCESS_KEY=` lines, an `AKIA` id, the UI's own secret values and a
`deploy/compose/.env` secret written *during* the job (as `make up` does); none of it may reach an SSE frame, a
fragment, a POST reply, the runner's view or a log line.

The runner is the one `build_deps` composes (its secret sources are the production ones: `Settings.secrets()` and
the compose `.env` re-read); only its targets are swapped for a `sh -c` script. No allowlisted target runs here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import shutil
from pathlib import Path

import httpx
import pytest
from demo_ui.app import create_app
from demo_ui.config import Settings
from demo_ui.deps import build_deps
from demo_ui.feed import Frame
from demo_ui.ops.allowlist import OpsTarget, by_name
from tower_consent import Store

from tests.privacy.patterns import AWS_KEY_ID, BEARER, E164_STRICT, phone_hits

from .fakes import Recorder

pytestmark = pytest.mark.integration

HX = {"HX-Request": "true", "Origin": "http://ui.test"}
UI_SECRETS = {
    "tower_bearer": "tower-bearer-planted-7f3a9c",
    "mock_admin_token": "mock-admin-planted-4b2e81",
    "alerts_bearer": "alerts-bearer-planted-9d0c55",
    "bind_admin_token": "bind-admin-planted-1a6f2e",
    "ui_token": "ui-token-planted-c83b07",
}
COMPOSE_SECRET = "compose-internal-planted-5e9d4a"
NUMBERS = (
    "+1 613 555 0101",
    "+1 (613) 555-0102",
    "+16135550103",
    "16135550104",
    "6135550105",
    "(613) 555-0106",
    "613.555.0107",
    "+447700900123",
)
LONG_BEARER = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJlLXBsYW50ZWQ"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
KEY_ID = "AKIAIOSFODNN7EXAMPLE"
STS_ID = "ASIAQWERTYUIOPASDFGH"


def script() -> str:
    numbers = "; ".join(f"echo 'call {n} now'" for n in NUMBERS)
    secrets = "; ".join(f"echo 'value {v}'; echo 'glued={v}x'" for v in UI_SECRETS.values())
    cut = "x" * 1990  # a secret straddling the 2,000-character cut
    return (
        f"{numbers}; "
        f"echo 'Authorization: Bearer {LONG_BEARER}'; echo 'bearer {LONG_BEARER}'; "
        f"echo 'AWS_SECRET_ACCESS_KEY={SECRET_KEY}'; echo 'export AWS_ACCESS_KEY_ID={KEY_ID}'; "
        f"echo 'id {KEY_ID} and {STS_ID}'; echo 'MOCK_ADMIN_TOKEN=abcdef123456'; "
        f'echo \'{{"INTERNAL_BEARER": "json-bearer-0123456789"}}\'; '
        f"{secrets}; echo '{cut}{UI_SECRETS['tower_bearer']}'; "
        "mkdir -p deploy/compose; "
        f"printf 'INTERNAL_BEARER={COMPOSE_SECRET}\\nTOWER_ENV=local\\n' > deploy/compose/.env; "
        f"echo 'compose wrote {COMPOSE_SECRET}'; "
        "echo done"
    )


def forbidden() -> tuple[str, ...]:
    return (
        *UI_SECRETS.values(),
        COMPOSE_SECRET,
        LONG_BEARER[:12],
        SECRET_KEY[:10],
        KEY_ID,
        STS_ID,
        "abcdef123456",
        "json-bearer-0123456789",
        "555 0101",
        "555-0102",
        "5550103",
        "5550104",
        "5550105",
        "555-0106",
        "555.0107",
        "7700900123",
    )


def hits(text: str) -> list[str]:
    found = [*phone_hits(text), *E164_STRICT.findall(text)]
    found += [m.group(0) for m in AWS_KEY_ID.finditer(text)] + BEARER.findall(text)
    return found + [s for s in forbidden() if s in text]


@pytest.mark.skipif(not Path("/bin/sh").exists(), reason="needs /bin/sh to run the planted script")
async def test_no_number_token_or_key_in_any_frame_fragment_reply_or_log(
    store: Store, lines: dict[str, str], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    root = tmp_path / "checkout"
    (root / "mk").mkdir(parents=True)
    (root / "Makefile").write_text("all:\n", encoding="utf-8")
    rec = Recorder()
    settings = Settings(checkout=root, **UI_SECRETS)
    deps = build_deps(
        settings,
        transports={
            "mock": rec.mock(),
            "binding": rec.binding(),
            "alerts": rec.alerts(),
            "ops": httpx.MockTransport(lambda _: httpx.Response(200)),
        },
        store=store,
        reset=None,
        mode="scripted",
        which=lambda _: "/usr/bin/make",
    )
    assert deps.ops.off is None
    deps.ops.targets = by_name(
        (OpsTarget("up", ("sh", "-c", script()), "local", "stack", "Start", False, ("base",)),)
    )
    q = deps.feed.subscribe()
    app = create_app(deps)
    try:
        # The browser's cookie after its first `?token=` visit (doc 11 §6).
        cookie = {"demo_ui_token": UI_SECRETS["ui_token"]}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://ui.test", cookies=cookie
        ) as c:
            started = await c.post("/ops/run/up", headers=HX)
            assert started.status_code == 202
            job = await deps.ops.task  # type: ignore[misc]
            assert job.state == "pass", list(deps.ops.backlog)
            replies = [
                started.text,
                (await c.get("/fragment/ops")).text,
                (await c.get("/fragment/ops-checklist")).text,
                (await c.post("/ops/stop", headers=HX)).text,
            ]
    finally:
        await deps.aclose()

    frames: list[Frame] = []
    while not q.empty():
        frames.append(q.get_nowait())
    backlog = list(deps.ops.backlog)
    assert backlog[0].startswith("call ") and backlog[-1] == "done", backlog  # every line went through
    assert len(backlog) == len(NUMBERS) + 7 + 2 * len(UI_SECRETS) + 3
    assert any(f.event == "ops" for f in frames) and frames[-1].event == "ops-status"
    assert "compose wrote ••••" in backlog  # the file written mid-job is re-read before the next line

    channels = {
        "sse": "\n".join(f.sse() for f in frames),
        "fragments": "\n".join(replies),
        "backlog": "\n".join(backlog),
        "view": repr(deps.ops.view()),
        "log": "\n".join(r.getMessage() for r in caplog.records) + caplog.text,
    }
    for name, text in channels.items():
        assert hits(text) == [], name


# --- The aws runner (prompt 23, doc 11 §11 "Privacy sweep (tester)") ---------------------------------------------
# One `aws-ecr-up` job through a fake `make` first on `PATH` prints, on stdout and stderr, every credential shape the
# redactor names (T7): `AKIA…`/`ASIA…` ids, the three shared-credentials names in any case with `=`/`:` and quotes,
# three 40-character secret-shaped runs (one with `/`, one with `+`, one digits only), a session-token-length run,
# E.164 numbers and bearers. The fake also answers the quiet probes, with planted secrets on stderr (discarded) and a
# sensitive Terraform output (filtered). Nothing may reach an SSE frame, a fragment, a POST reply, the backlog, the
# runner's view, the transcript files or a log line.
AWS_SECRETS = (
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "je7MtGbClwBF+2Zp9Utkh3yCo8nvbEXAMPLEKEYa",
    "Hq3kT9vLmP2xR7wZ5nB8cY4dF6gJ1sA0eU3iO9tK",
)
SESSION = "FwoGZXIvYXdzEXAMPLE" + "Zm9vYmFyU2Vzc2lvbg" * 8
KEY_IDS = ("AKIAIOSFODNN7EXAMPLE", "ASIAQWERTYUIOPASDFGH", "AKIAZZZZEXAMPLE00001")
AWS_NUMBERS = ("+16135550101", "+1 613 555 0102", "+44 7700 900123", "6135550104")
ACCOUNT = "123456789012"


def aws_lines() -> tuple[str, ...]:
    s1, s2, s3 = AWS_SECRETS
    k1, k2, k3 = KEY_IDS
    return (
        f"id {k1} and {k2}",
        f"aws_access_key_id = {k3}",
        f"aws_secret_access_key={s1}",
        f"AWS_SECRET_ACCESS_KEY: '{s2}'",
        f'"aws_secret_access_key": "{s3}"',
        f"Aws_Session_Token = {SESSION}",
        f'aws_session_token: "{SESSION}"',
        f"export AWS_SESSION_TOKEN={SESSION}",
        f"bare {s1} end",
        f"[{s2}]",
        f"'{s3}'",
        SESSION,
        f"aws configure set aws_secret_access_key {s1}",
        *(f"call {n} now" for n in AWS_NUMBERS),
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.e30.EXAMPLEsignatureplanted",
        f"COGNITO_PASSWORD_ASISH={s2}",
        f"TF_VAR_demo_admin_bearer={s3}",
    )


def _echo_all(lines: tuple[str, ...], to: str = "") -> str:
    return "; ".join("printf '%s\\n' " + shlex.quote(line) + to for line in lines)


def aws_fake_make() -> str:
    names = json.dumps(["att-dev/mock-carrier", "att-dev/tower-mcp", "att-dev/binding-page", "att-dev/alerts",
                        "att-dev/ref-client"])  # fmt: skip
    tf = json.dumps({
        "region": {"value": "us-east-1", "sensitive": False},
        "sms_sandbox_commands": {"value": f"{AWS_NUMBERS[0]} {AWS_SECRETS[0]}", "sensitive": True},
        "access": {"value": KEY_IDS[0], "sensitive": False},
    })  # fmt: skip
    planted_err = _echo_all(aws_lines(), " >&2")
    return f"""#!/bin/bash
last="${{@: -1}}"
if [ "$last" = "__ops_probe" ]; then
  {planted_err}
  case "$5" in
    *COGNITO*) echo "profile= region=us-east-1 keys=1 cognito=11" ;;
    *"sts get-caller-identity"*) echo {ACCOUNT} ;;
    *"ecr describe-repositories"*) echo {shlex.quote(names)} ;;
    *terraform*) echo {shlex.quote(tf)} ;;
    *) echo __ops_missing__ ;;
  esac
  exit 0
fi
case " $* " in *" config-check "*) {planted_err}; exit 0 ;; esac
echo "argv: $*"
{_echo_all(aws_lines())}
{planted_err}
echo done
exit 0
"""


def aws_forbidden() -> tuple[str, ...]:
    return (
        *KEY_IDS,
        *(s[:12] for s in AWS_SECRETS),
        *(s[-12:] for s in AWS_SECRETS),
        SESSION[:20],
        SESSION[-20:],
        "EXAMPLEsignatureplanted",
        "ui-EXAMPLE-parent-secret",
        "5550101",
        "555 0102",
        "7700 900123",
        "5550104",
        ACCOUNT,
    )


def aws_hits(text: str) -> list[str]:
    found = [*phone_hits(text), *E164_STRICT.findall(text)]
    found += [m.group(0) for m in AWS_KEY_ID.finditer(text)] + BEARER.findall(text)
    return found + [s for s in aws_forbidden() if s in text]


@pytest.mark.skipif(not Path("/bin/bash").exists(), reason="needs /bin/bash for the fake make")
async def test_aws_runner_no_key_secret_token_or_number_in_any_channel(
    store: Store, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    root = tmp_path / "checkout"
    (root / "mk").mkdir(parents=True)
    (root / "Makefile").write_text("all:\n", encoding="utf-8")
    (root / ".env").write_text("AWS_REGION=us-east-1\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "make"
    fake.write_text(aws_fake_make(), encoding="utf-8")
    fake.chmod(0o755)
    path = f"{bin_dir}:/usr/bin:/bin"
    assert shutil.which("make", path=path) == str(fake)  # the fake, never the real one
    rec = Recorder()
    deps = build_deps(
        Settings(checkout=root, aws_ops="1", **UI_SECRETS),
        transports={
            "mock": rec.mock(),
            "binding": rec.binding(),
            "alerts": rec.alerts(),
            "ops": httpx.MockTransport(lambda _: httpx.Response(200)),
        },
        store=store,
        reset=None,
        mode="scripted",
        environ={"PATH": path, "HOME": str(root), "AWS_SECRET_ACCESS_KEY": "ui-EXAMPLE-parent-secret"},
        which=lambda name: shutil.which(name, path=path),
    )
    q = deps.feed.subscribe()
    app = create_app(deps)
    try:
        cookie = {"demo_ui_token": UI_SECRETS["ui_token"]}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://ui.test", cookies=cookie
        ) as c:
            header = (await c.get("/fragment/ops-header?fresh=1")).text
            assert "AWS runs: on" in header and "1234-5678-9012" in header, header  # the probes ran
            started = await c.post("/ops/run/aws-ecr-up", headers=HX)
            assert started.status_code == 202, started.text
            job = await asyncio.wait_for(deps.ops.task, 30)  # type: ignore[arg-type]
            assert job.state == "pass", list(deps.ops.backlog)
            replies = [header, started.text]
            for frag in ("ops", "ops-header", "ops-checklist", "ops-teardown"):
                replies.append((await c.get(f"/fragment/{frag}?fresh=1")).text)
                replies.append((await c.get(f"/fragment/{frag}")).text)
    finally:
        await deps.aclose()

    frames: list[Frame] = []
    while not q.empty():
        frames.append(q.get_nowait())
    backlog = list(deps.ops.backlog)
    assert backlog[-1] == "done" and len(backlog) >= 2 * len(aws_lines()) + 2, (
        backlog
    )  # stdout and stderr arrived
    assert any(f.event == "ops" for f in frames) and any(f.event == "ops-status" for f in frames)
    logs = sorted((root / "artifacts" / "ops-log").glob("*-aws-ecr-up.log"))
    assert len(logs) == 1
    transcript = logs[0].read_text(encoding="utf-8")
    assert " start aws-ecr-up: make ecr-up ENV=aws" in transcript and " end pass exit 0 " in transcript
    assert "aws_secret_access_key=••••" in transcript and "aws_session_token=••••" in transcript.lower()

    channels = {
        "sse": "\n".join(f.sse() for f in frames),
        "fragments": "\n".join(replies),
        "backlog": "\n".join(backlog),
        "view": repr(deps.ops.view()),
        "transcript": transcript,
        "log": "\n".join(r.getMessage() for r in caplog.records) + caplog.text,
    }
    for name, text in channels.items():
        assert aws_hits(text) == [], name
