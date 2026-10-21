"""ENV=aws: an aws job that rewrote deploy/.env.aws makes the UI re-exec itself (doc 11 §2)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from demo_ui.config import Settings
from demo_ui.deps import aws_file_values, settings_reloader


def _job(runner: str = "aws") -> SimpleNamespace:
    return SimpleNamespace(target=SimpleNamespace(runner=runner, name="aws-deploy"), state="pass")


@pytest.mark.unit
def test_reloads_only_when_the_aws_keys_changed(tmp_path: Path) -> None:
    f = tmp_path / ".env.aws"
    f.write_text("MOCK_URL=https://a.example/carrier-admin\nDEMO_ADMIN_BEARER=old\nOTHER=1\n")
    published: list[tuple[str, str]] = []
    fired: list[str] = []
    on_end = settings_reloader(
        Settings(env="aws"), f, publish=lambda t, h: published.append((t, h)), reexec=lambda: fired.append("x"), delay_s=0
    )

    async def run() -> None:
        on_end(_job())  # unchanged: nothing
        f.write_text("MOCK_URL=https://a.example/carrier-admin\nDEMO_ADMIN_BEARER=old\nOTHER=2\n")
        on_end(_job())  # a key the UI does not load: nothing
        f.write_text("MOCK_URL=https://b.example/carrier-admin\nDEMO_ADMIN_BEARER=new\n")
        on_end(_job("local"))  # not an aws job: nothing
        on_end(_job())
        await asyncio.sleep(0.01)

    asyncio.run(run())
    assert fired == ["x"] and len(published) == 1
    topic, html = published[0]
    assert topic == "ops" and "deploy/.env.aws changed (DEMO_ADMIN_BEARER, MOCK_URL)" in html
    assert "new" not in html.split("changed")[1].split(")")[1]  # names only, never the values


@pytest.mark.unit
def test_local_env_never_reloads_and_a_missing_file_reads_empty(tmp_path: Path) -> None:
    fired: list[str] = []
    on_end = settings_reloader(Settings(env="local"), tmp_path / "x", publish=lambda *_: None, reexec=lambda: fired.append("x"))
    on_end(_job())
    assert fired == [] and aws_file_values(tmp_path / "missing") == {} and aws_file_values(None) == {}
