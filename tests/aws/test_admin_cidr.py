"""Doc 11 §2 "admin-open / admin-close", doc 10 §5, §11: `scripts/admin_cidr.py` (the make targets' tfvars editor)
and the two recipes. Offline: temp files; the recipes run in a temp copy of the Makefile with fake `terraform`, `uv`
and `curl` first on PATH, so no real terraform, no AWS and no network are ever touched.

- open replaces the one line with the marker and keeps every other byte, the mode and atomicity; close writes `[]`;
  an absent line is appended; an absent file → exit 2 and nothing created; two lines or a multi-line list → exit 2
  and the file unchanged; invalid / private / documentation / IPv6 / `/24` → exit 2; the address is never printed;
- the plan gate: a destroying (or unreadable) plan stops with exit 3 unless FORCE=1; `admin-close` asks unless
  FORCE=1.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.aws.conftest import ROOT

TFVARS = 'region = "us-east-1"\r\nname_prefix = "att"\n\n# the admin path\ndemo_admin_cidrs = [] # e.g. ["203.0.113.7/32"]\nenvironment = "dev"'
OPENED = 'demo_admin_cidrs = ["8.8.8.8/32"] # managed by make admin-open / admin-close'
CLOSED = "demo_admin_cidrs = [] # managed by make admin-open / admin-close"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("admin_cidr", ROOT / "scripts" / "admin_cidr.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ac = _load()


def _never() -> str:
    raise AssertionError("checkip must not be asked")


@pytest.fixture
def tfvars(tmp_path: Path) -> Path:
    p = tmp_path / "aws.tfvars"
    p.write_bytes(TFVARS.encode())
    p.chmod(0o640)
    return p


@pytest.mark.unit
def test_open_replaces_the_one_line_and_keeps_every_other_byte(
    tfvars: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ac.main(["open", "--cidr", "8.8.8.8", "--file", str(tfvars)], fetch=_never) == 0
    after = tfvars.read_bytes().decode()
    assert after == TFVARS.replace('demo_admin_cidrs = [] # e.g. ["203.0.113.7/32"]', OPENED)
    assert stat.S_IMODE(tfvars.stat().st_mode) == 0o640
    out = capsys.readouterr()
    assert out.out == "demo_admin_cidrs: 1 address\n" and "8.8.8.8" not in out.out + out.err
    assert [p.name for p in tfvars.parent.iterdir()] == ["aws.tfvars"]  # the temp file is gone (os.replace)
    assert ac.main(["close", "--file", str(tfvars)]) == 0
    assert tfvars.read_bytes().decode() == TFVARS.replace(
        'demo_admin_cidrs = [] # e.g. ["203.0.113.7/32"]', CLOSED
    )
    assert capsys.readouterr().out == "demo_admin_cidrs: []\n"


@pytest.mark.unit
def test_open_without_a_cidr_asks_checkip(tfvars: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ac.main(["open", "--cidr", "", "--file", str(tfvars)], fetch=lambda: "1.1.1.1\n") == 0
    assert '["1.1.1.1/32"]' in tfvars.read_text() and "1.1.1.1" not in capsys.readouterr().out
    assert ac.main(["open", "--file", str(tfvars)], fetch=lambda: "") == 2  # curl failed: refuse


@pytest.mark.unit
def test_an_absent_line_is_appended(tmp_path: Path) -> None:
    p = tmp_path / "aws.tfvars"
    p.write_text('region = "us-east-1"')  # no trailing newline
    assert ac.main(["close", "--file", str(p)]) == 0
    assert p.read_text() == f'region = "us-east-1"\n{CLOSED}\n'


@pytest.mark.unit
def test_an_absent_file_is_refused_and_not_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "aws.tfvars"
    assert ac.main(["open", "--cidr", "8.8.8.8", "--file", str(p)], fetch=_never) == 2
    assert ac.main(["close", "--file", str(p)]) == 2
    assert not p.exists() and list(tmp_path.iterdir()) == []
    assert (
        "deploy/terraform/envs/aws.tfvars not found: copy envs/aws.tfvars.example first"
        in capsys.readouterr().err
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "demo_admin_cidrs = []\ndemo_admin_cidrs = []\n",
        'demo_admin_cidrs = [\n  "8.8.8.8/32",\n]\n',
        "demo_admin_cidrs = var.x\n",
    ],
)
def test_two_lines_or_a_multi_line_list_are_refused(
    tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "aws.tfvars"
    p.write_text(text)
    assert ac.main(["open", "--cidr", "8.8.8.8", "--file", str(p)], fetch=_never) == 2
    assert p.read_text() == text and "edit demo_admin_cidrs by hand to one line" in capsys.readouterr().err


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        "10.0.0.1",
        "192.168.1.2/32",
        "127.0.0.1",
        "203.0.113.7",
        "8.8.8.0/24",
        "::1",
        "2001:db8::1",
        "x",
        "1.2.3.4 5",
    ],
)
def test_only_a_public_ipv4_slash32(tfvars: Path, bad: str, capsys: pytest.CaptureFixture[str]) -> None:
    before = tfvars.read_bytes()
    assert ac.main(["open", "--cidr", bad, "--file", str(tfvars)], fetch=_never) == 2
    out = capsys.readouterr()
    assert tfvars.read_bytes() == before and "ADMIN_CIDR is not a public IPv4 /32" in out.err
    assert bad not in out.out + out.err.replace("ADMIN_CIDR is not a public IPv4 /32", "")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("plan", "force", "code"),
    [
        ("Plan: 1 to add, 2 to change, 0 to destroy.\n", False, 0),
        ("No changes.\n", False, 0),
        ("Plan: 1 to add, 0 to change, 3 to destroy.\n", False, 3),
        ("Plan: 1 to add, 0 to change, 3 to destroy.\n", True, 0),
        ("Plan: 0 to add, 0 to change, 1 to destroy.\nlater\nPlan: 1 to import, 2 to add, 0 to change, 0 to destroy.\n",
         False, 0),  # the last summary counts
        ("NOT RUN\n", False, 3),  # unreadable counts as destroying
        (None, False, 3),
    ],
)  # fmt: skip
def test_plan_gate(
    tmp_path: Path, plan: str | None, force: bool, code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "terraform-plan.txt"
    if plan is not None:
        p.write_text(plan)
    args = ["plan-gate", "--name", "admin-open", "--plan", str(p), *(["--force"] if force else [])]
    assert ac.main(args) == code
    if code == 3:
        err = capsys.readouterr().err
        assert err.startswith("admin-open: the plan destroys ") and "replaces DEMO_ADMIN_BEARER" in err


# --- the recipes, in a temp copy of the Makefile -------------------------------------------------------------------

FAKE_TF = """#!/bin/sh
echo "terraform $*" >> "$LOG"
case "$*" in
  *" show "*) cat "$PLAN_TEXT" ;;
  *" output -json"*) echo '{}' ;;
esac
exit 0
"""
FAKE_UV = """#!/bin/sh
# `uv run python <script> …`: run admin_cidr.py for real (in the temp checkout), log everything else
shift 2
case "$1" in
  scripts/admin_cidr.py) exec python3 "$@" ;;
  *) echo "uv $*" >> "$LOG" ;;
esac
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def checkout(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    if shutil.which("make") is None:
        pytest.skip("make not on PATH")
    root = tmp_path / "co"
    shutil.copytree(ROOT / "mk", root / "mk")
    shutil.copy(ROOT / "Makefile", root / "Makefile")
    (root / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "admin_cidr.py", root / "scripts" / "admin_cidr.py")
    envs = root / "deploy" / "terraform" / "envs"
    envs.mkdir(parents=True)
    (envs / "aws.tfvars").write_text('region = "us-east-1"\ndemo_admin_cidrs = []\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "terraform", FAKE_TF)
    _exe(bin_dir / "uv", FAKE_UV)
    _exe(bin_dir / "curl", "#!/bin/sh\necho 8.8.8.8\n")
    _exe(bin_dir / "docker", "#!/bin/sh\nexit 0\n")
    plan = tmp_path / "plan.txt"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "LOG": str(tmp_path / "calls.log"),
           "PLAN_TEXT": str(plan)}  # fmt: skip
    assert shutil.which("terraform", path=env["PATH"]) == str(bin_dir / "terraform")
    return root, env


MAKE = ("make", "-s", "--no-print-directory")  # resolved from the test's PATH: fake tools first, on purpose


def _make(root: Path, env: dict[str, str], *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    argv = [*MAKE, *args]
    return subprocess.run(  # noqa: S603 - fixed argv in a temp checkout, fake terraform/uv/curl first on PATH
        argv, cwd=root, env=env, input=stdin, capture_output=True, text=True, timeout=60, check=False
    )


def _calls(env: dict[str, str]) -> str:
    log = Path(env["LOG"])
    return log.read_text() if log.exists() else ""


@pytest.mark.integration
def test_admin_open_stops_with_exit_3_on_a_destroying_plan_unless_force(
    checkout: tuple[Path, dict[str, str]],
) -> None:
    root, env = checkout
    Path(env["PLAN_TEXT"]).write_text("Plan: 3 to add, 1 to change, 2 to destroy.\n")
    r = _make(root, env, "admin-open", "ENV=aws", "ADMIN_CIDR=8.8.8.8")
    assert r.returncode != 0 and "the plan destroys 2 resource(s)" in r.stderr
    assert "make: *** [mk/aws.mk" in r.stderr and "Error 3" in r.stderr
    assert OPENED in (root / "deploy/terraform/envs/aws.tfvars").read_text()
    assert " apply " not in _calls(env) and "8.8.8.8" not in r.stdout
    r = _make(root, env, "admin-open", "ENV=aws", "ADMIN_CIDR=8.8.8.8", "FORCE=1")
    assert r.returncode == 0, r.stderr
    assert " apply " in _calls(env)  # deploy ran (against the fake terraform)


@pytest.mark.integration
def test_admin_open_without_destroys_deploys_and_bad_cidr_stops_first(
    checkout: tuple[Path, dict[str, str]],
) -> None:
    root, env = checkout
    Path(env["PLAN_TEXT"]).write_text("Plan: 0 to add, 1 to change, 0 to destroy.\n")
    r = _make(root, env, "admin-open", "ENV=aws")  # no ADMIN_CIDR: checkip (the fake curl)
    assert r.returncode == 0, r.stderr
    assert "demo_admin_cidrs: 1 address" in r.stdout and " apply " in _calls(env)
    Path(env["LOG"]).unlink()
    r = _make(root, env, "admin-open", "ENV=aws", "ADMIN_CIDR=10.0.0.1/32")
    assert r.returncode != 0 and "ADMIN_CIDR is not a public IPv4 /32" in r.stderr and _calls(env) == ""


@pytest.mark.integration
def test_admin_close_asks_unless_force(checkout: tuple[Path, dict[str, str]]) -> None:
    root, env = checkout
    Path(env["PLAN_TEXT"]).write_text("Plan: 0 to add, 1 to change, 4 to destroy.\n")
    tfvars = root / "deploy/terraform/envs/aws.tfvars"
    tfvars.write_text('demo_admin_cidrs = ["8.8.8.8/32"]\n')
    r = _make(root, env, "admin-close", "ENV=aws", stdin="n\n")
    assert r.returncode != 0 and _calls(env) == "" and "8.8.8.8" in tfvars.read_text()  # asked, refused
    r = _make(root, env, "admin-close", "ENV=aws", "FORCE=1")
    assert r.returncode == 0, r.stderr
    assert tfvars.read_text() == f"{CLOSED}\n" and " apply " in _calls(env)


@pytest.mark.unit
def test_the_recipes_say_what_the_doc_says() -> None:
    mk = (ROOT / "mk" / "aws.mk").read_text()
    open_ = mk.split("\nadmin-open:", 1)[1].split("\nadmin-close:", 1)[0]
    close = mk.split("\nadmin-close:", 1)[1]
    assert "scripts/admin_cidr.py open" in open_ and "plan-gate --name admin-open" in open_
    assert "$(if $(filter 1,$(FORCE)),--force)" in open_ and open_.index("plan ENV=aws") < open_.index(
        "plan-gate"
    )
    assert 'read -p "close the demo admin path (plan, then deploy)? [y/N] "' in close
    assert '[ "$(FORCE)" != "1" ]' in close and "scripts/admin_cidr.py close" in close
    demo = (ROOT / "mk" / "demo.mk").read_text()
    ops_ui = demo.split("\nops-ui:", 1)[1].split("\nshowcase-alexa:", 1)[0]
    assert (
        "DEMO_UI_AWS_OPS=1" in ops_ui
        and "DEMO_UI_HOST=127.0.0.1" in ops_ui
        and "require-env" not in ops_ui.split("\n")[0]
    )
    assert os.access(ROOT / "scripts" / "admin_cidr.py", os.R_OK)
