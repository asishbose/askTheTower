"""Doc 11 §2 "Env modes" (T3, reading R1), §11: the make loader. `loader_argv` is exactly the `make --eval` argv;
against a temp checkout whose Makefile includes a copy of the real `mk/vars.mk` and a fake `.env`, a probe recipe
that prints `env` proves the `.env` values reach the child while the UI's own planted secret does not; `facts` says
names and booleans only; no planted value reaches a log line, a `ProbeResult`, the header fragment or a status
frame; `envload.py`, `probes.py` and `gate.py` never read a file; no `.env` → the gate is off with G5's reason."""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import shutil
from pathlib import Path

import pytest
from demo_ui import views
from demo_ui.ops import envload, gate, probes
from demo_ui.ops.envload import FACTS_RECIPE, Facts, dotenv_present, loader_argv, parse_facts
from demo_ui.ops.gate import G5, Gate
from demo_ui.ops.probes import ProbeExec, ProbeResult, Probes

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
KEY_ID = "AKIAIOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
PASSWORD = "Cognito-EXAMPLE-pw-0042"
UI_SECRET = "ui-process-EXAMPLE-secret-77"
DOTENV = (
    f"AWS_ACCESS_KEY_ID={KEY_ID}\nAWS_SECRET_ACCESS_KEY={SECRET}\nAWS_PROFILE=att\nAWS_REGION=us-east-1\r\n"
    f"COGNITO_PASSWORD_ASISH={PASSWORD}\nCOGNITO_PASSWORD_MOM=\nDEMO_UI_AWS_OPS=\n"
)
PLANTED = (KEY_ID, SECRET, PASSWORD, UI_SECRET)
needs_make = pytest.mark.skipif(
    shutil.which("make") is None or shutil.which("bash") is None, reason="needs make"
)


def checkout(tmp_path: Path, dotenv: str | None = DOTENV) -> Path:
    root = tmp_path / "checkout"
    (root / "mk").mkdir(parents=True)
    shutil.copy(ROOT / "mk" / "vars.mk", root / "mk" / "vars.mk")
    (root / "Makefile").write_text("SHELL := /bin/bash\ninclude mk/vars.mk\n", encoding="utf-8")
    if dotenv is not None:
        (root / ".env").write_text(dotenv, encoding="utf-8")
    return root


def parent() -> dict[str, str]:
    """The UI's own environment: what `make ops-ui` exported into it, plus a secret of its own."""
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG")}
    return {**base, "AWS_SECRET_ACCESS_KEY": UI_SECRET, "AWS_PROFILE": "from-the-shell", "ENV": "aws"}


def test_loader_argv_is_exactly_one_rule_and_one_goal() -> None:
    assert loader_argv("aws", "env") == (
        "make", "-s", "--no-print-directory", "ENV=aws", "--eval", "__ops_probe: ; @env", "__ops_probe",
    )  # fmt: skip
    argv = loader_argv("local", FACTS_RECIPE)
    assert argv[3] == "ENV=local" and argv[-1] == "__ops_probe" and argv.count("--eval") == 1
    assert "sh" not in argv and "-c" not in argv  # no shell of ours: make runs the recipe


@needs_make
def test_values_reach_the_child_and_never_the_ui(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    root = checkout(tmp_path)
    ran = asyncio.run(ProbeExec(root, parent()).run(loader_argv("aws", "env"), timeout_s=20))
    assert ran.code == 0, ran
    env = dict(line.split("=", 1) for line in ran.out.decode().splitlines() if "=" in line)
    assert (
        env["AWS_ACCESS_KEY_ID"] == KEY_ID and env["AWS_SECRET_ACCESS_KEY"] == SECRET
    )  # from .env, via make
    assert env["AWS_PROFILE"] == "att" and env["AWS_REGION"] == "us-east-1"  # CR stripped by the loader
    assert "COGNITO_PASSWORD_MOM" not in env  # empty values are skipped
    assert UI_SECRET not in ran.out.decode() and "from-the-shell" not in ran.out.decode()
    assert all(v not in caplog.text for v in PLANTED)


@needs_make
def test_facts_says_names_and_booleans_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    root = checkout(tmp_path)
    p = Probes(root, parent(), timeout_s=20)
    result = asyncio.run(p.facts())
    assert result == ProbeResult(True, Facts("att", "us-east-1", True, 1))
    assert result.value is not None and result.value.credentials == "profile att"
    html = views.render(
        "ops_header.html", gate=None, credentials=result.value.credentials, region=result.value.region,
        facts_reason="", account="account unknown: aws CLI not found (runbook 0.8)", banner=None,
    )  # fmt: skip
    status = views.render("status.html", text=repr(result), level="info")
    for channel in (repr(result), result.reason, caplog.text, html, status):
        assert all(v not in channel for v in PLANTED), channel
    assert "profile att" in html and "us-east-1" in html


@needs_make
def test_facts_with_the_key_pair_and_no_profile(tmp_path: Path) -> None:
    root = checkout(
        tmp_path, DOTENV.replace("AWS_PROFILE=att", "AWS_PROFILE=").replace("MOM=", f"MOM={PASSWORD}")
    )
    result = asyncio.run(Probes(root, parent(), timeout_s=20).facts())
    assert (
        result.value == Facts("", "us-east-1", True, 2) and result.value.credentials == "key pair from .env"
    )


def test_parse_facts_refuses_anything_else() -> None:
    assert parse_facts("profile= region= keys= cognito=\n") == Facts("", "", False, 0)
    assert parse_facts("profile=att region=us-east-1 keys=1 cognito=11") == Facts("att", "us-east-1", True, 2)
    for bad in (
        f"profile=att region=us-east-1 keys={SECRET} cognito=11",
        "profile=a b region= keys= cognito=",
        "profile=att region=us-east-1 keys=1 cognito=11\nextra",
        "",
    ):
        assert parse_facts(bad) is None, bad
    assert Facts("", "", False, 0).credentials == "default chain"


def test_missing_dotenv_turns_the_gate_off_with_g5(tmp_path: Path) -> None:
    root = checkout(tmp_path, dotenv=None)
    assert not dotenv_present(root)
    asked: list[int] = []

    async def config_check() -> ProbeResult[None]:
        asked.append(1)
        return ProbeResult(True)

    g = Gate(published_loopback=False, host="127.0.0.1", flag="1", checkout=root, which=lambda _: "/usr/bin/make",
             config_check=config_check)  # fmt: skip
    assert asyncio.run(g.reason()) == G5 and asked == []


def test_envload_probes_and_gate_never_read_a_file() -> None:
    """They may ask whether `.env` exists (and its mtime); they never open, read or parse it (T3)."""
    readers = {"open", "read_text", "read_bytes", "read_env_file", "load_dotenv", "dotenv_values"}
    for module in (envload, probes, gate):
        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
                assert name not in readers, (module.__name__, name)
    source = Path(envload.__file__ or "").read_text(encoding="utf-8")
    assert "(checkout / ROOT_ENV).exists()" in source and "(checkout / ROOT_ENV).stat()" in source
