"""Doc 11 §11 "Pane 5 … Privacy": the ops stream sweep. One job prints planted E.164 numbers (grouped and bare),
`Bearer` values, `*_TOKEN=`/`AWS_SECRET_ACCESS_KEY=` lines, an `AKIA` id, the UI's own secret values and a
`deploy/compose/.env` secret written *during* the job (as `make up` does); none of it may reach an SSE frame, a
fragment, a POST reply, the runner's view or a log line.

The runner is the one `build_deps` composes (its secret sources are the production ones: `Settings.secrets()` and
the compose `.env` re-read); only its targets are swapped for a `sh -c` script. No allowlisted target runs here.
"""

from __future__ import annotations

import logging
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
    deps.ops.targets = by_name((OpsTarget("up", ("sh", "-c", script()), "stack", "Start", ("base",), False),))
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
