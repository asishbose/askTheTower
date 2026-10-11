"""The one settings file (root .env): the checker, make's loader (mk/vars.mk) and the compose overlay (init-env.sh)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("config_check", ROOT / "scripts" / "config_check.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cc = _load()


def test_example_is_clean_and_every_tf_var_exists() -> None:
    text = (ROOT / ".env.example").read_text("utf-8")
    rows, errors = cc.parse(text)
    assert not errors
    assert all(v == "" or k in {"AWS_REGION"} for _, k, v in rows), (
        "the template sets no values but the region"
    )
    tf_vars = (ROOT / "deploy/terraform/variables.tf").read_text() + (
        ROOT / "deploy/terraform/eks/variables.tf"
    ).read_text()
    for _, key, _ in rows:
        if key.startswith("TF_VAR_"):
            assert f'variable "{key[7:]}"' in tf_vars, key


def test_compose_keys_in_template_exist_in_compose_example() -> None:
    compose = {k for _, k, _ in cc.parse((ROOT / "deploy/compose/.env.example").read_text())[0]}
    root = {k for _, k, _ in cc.parse((ROOT / ".env.example").read_text())[0]}
    local = {s: keys for s, keys in cc.sections((ROOT / ".env.example").read_text())}
    for title in (
        "Local stack secrets (deploy/compose/.env). Empty = `make up` generates a random value per machine",
    ):
        assert set(local[title]) <= compose
    assert {"TOWER_BEARER", "INTERNAL_BEARER", "SESSION_SECRET"} <= root & compose


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        ("A=zq9$y", "'$' and '#'"),
        ("A=zq9#y", "'$' and '#'"),
        ('A="zq9"', "no quotes"),
        ("A= zq9", "whitespace"),
        ("A=zq9\\", "trailing backslash"),
        ("A = zq9", "not KEY=value"),
        ("1A=zq9", "not KEY=value"),
    ],
)
def test_format_errors_name_the_line_not_the_value(line: str, fragment: str) -> None:
    _, errors = cc.parse(f"OK=1\n{line}\n")
    assert len(errors) == 1 and fragment in errors[0] and "line 2" in errors[0]
    assert "zq9" not in errors[0]


def test_check_masks_secrets_and_flags_aws_mistakes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "AWS_ACCESS_KEY_ID=AKIAEXAMPLEEXAMPLE00\r\nTOWER_BEARER=s3cr3t-value\r\nTOWER_TZ=Europe/London\r\nTYPO=1\r\n"
        'TF_VAR_sms_sandbox_numbers=["+15555550100"]\r\n'
    )
    assert cc.check(env, ROOT / ".env.example") == 1
    out, err = capsys.readouterr()
    assert "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set together" in err
    assert "s3cr3t" not in out + err and "AKIA" not in out + err and "5555550100" not in out + err
    assert "Europe/London" in out and "TYPO: not in" in out and "CRLF" in out


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert cc.check(tmp_path / ".env", ROOT / ".env.example") == 0


@pytest.fixture
def scratch_make(tmp_path: Path) -> Path:
    if not shutil.which("make"):
        pytest.skip("make not installed")
    shutil.copy(ROOT / "mk" / "vars.mk", tmp_path / "vars.mk")
    (tmp_path / "Makefile").write_text(
        'include vars.mk\nshow:\n\t@echo "A=[$$A] P=[$$AWS_PROFILE] L=[$$TF_VAR_l] E=$(ENV)"\n'
    )
    return tmp_path


def _make(d: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    full = {**os.environ, **env}
    full.pop("ENV", None)
    return subprocess.run(  # noqa: S603
        ["make", "-s", "-C", str(d), *args],  # noqa: S607
        capture_output=True,
        text=True,
        env=full,
    )


def test_make_loads_exports_and_respects_precedence(scratch_make: Path) -> None:
    (scratch_make / ".env").write_text('A=file\r\nAWS_PROFILE=\r\n# A=comment\r\nTF_VAR_l=["+1","b"]\r\n')
    r = _make(scratch_make, "show", A="shell", AWS_PROFILE="mine")
    assert r.stdout.strip() == 'A=[file] P=[mine] L=[["+1","b"]] E=local', r.stderr
    assert _make(scratch_make, "show", "A=cli").stdout.startswith("A=[cli]")


def test_make_refuses_dollar_without_echoing_it(scratch_make: Path) -> None:
    (scratch_make / ".env").write_text("A=ok\nB=pa$$word\n")
    r = _make(scratch_make, "show")
    assert r.returncode != 0 and "line(s) 2" in r.stderr and "pa" not in r.stderr.split("line(s) 2", 1)[1]


def test_compose_overlay_takes_root_values_only_for_compose_keys(tmp_path: Path) -> None:
    for name in ("init-env.sh", ".env.example"):
        shutil.copy(ROOT / "deploy" / "compose" / name, tmp_path / name)
    root = tmp_path / "root.env"

    def run() -> None:
        subprocess.run(  # noqa: S603
            ["bash", str(tmp_path / "init-env.sh")],  # noqa: S607
            env={**os.environ, "ATT_ROOT_ENV": str(root)},
            check=True,
        )

    root.write_text("")
    run()
    generated = dict(line.split("=", 1) for line in (tmp_path / ".env").read_text().splitlines())
    root.write_text("TOWER_BEARER=mine\r\nSESSION_SECRET=\nAWS_SECRET_ACCESS_KEY=never-copied\n")
    run()
    after = dict(line.split("=", 1) for line in (tmp_path / ".env").read_text().splitlines())
    assert after["TOWER_BEARER"] == "mine"
    assert after["SESSION_SECRET"] == generated["SESSION_SECRET"] != ""
    assert "AWS_SECRET_ACCESS_KEY" not in after
    assert set(after) == set(generated)
