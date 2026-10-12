"""Doc 11 §2 "The Ops tab", §11 (prompts 22 and 23): the allowlist is the doc's table (all ten columns, 11 local and
18 aws rows); local argv is `make <name> ENV=local`, aws argv `make <target> ENV=aws` (+ the one fixed SEED_ARGS);
env modes per runner; confirm words on the destructive four; `after` names earlier aws rows; no "never" name; no
denied key in an env set (and adding one fails at import); the child env is an allow-list; `mask_secrets`."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
from demo_ui.ops import allowlist
from demo_ui.ops.allowlist import (
    AWS_MAKE_TARGETS,
    DENY,
    ENV_SETS,
    FIXED_ENV,
    LOCAL_NEVER,
    NEVER,
    TARGETS,
    AllowlistError,
    OpsTarget,
    by_name,
    check,
    child_env,
)
from demo_ui.redact import HIDDEN, mask_secrets

pytestmark = pytest.mark.unit

DOC = Path(__file__).resolve().parents[3] / "docs" / "architecture" / "components" / "11-demo-ui.md"
DOC_NEVER = {"ecr-outputs", "down-eks", "deploy-eks", "register-gateway", "test-nightly", "showcase-infra",
             "secrets-check"}  # fmt: skip
T2 = {"ecr-up", "push", "plan", "deploy", "outputs", "cognito-users", "seed-aws", "web-chat-sync", "web-chat-url",
      "demo", "latency-aws", "test-e2e", "test-report", "down", "down-all", "admin-open", "admin-close"}  # fmt: skip

Row = tuple[
    str, tuple[str, ...], str, str, str, bool, tuple[str, ...], str, str | None, str | None, tuple[str, ...]
]


def _confirm(cell: str) -> tuple[str | None, str | None]:
    if cell == "—":
        return None, None
    word = re.findall(r"`([^`]+)`", cell)[0]
    return word, ("plan_destroys" if "if the plan destroys" in cell else "always")


def doc_table() -> list[Row]:
    text = DOC.read_text(encoding="utf-8")
    block = text.split("<!-- ops-allowlist:start -->", 1)[1].split("<!-- ops-allowlist:end -->", 1)[0]
    rows = [r for r in block.strip().splitlines() if r.startswith("|")][2:]  # header and rule
    out: list[Row] = []
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        name, argv, runner, group, label, needs, env, mode, confirm, after = cells
        argv = argv.strip("`").replace(" ADMIN_CIDR=<cidr>", "")  # added at run time, after validation
        word, when = _confirm(confirm)
        out.append(
            (
                name.strip("`"), tuple(argv.split()), runner, group, label, needs == "yes",
                tuple(e.strip() for e in env.split(",")), mode, word, when,
                () if after == "—" else tuple(a.strip().strip("`") for a in after.split(",")),
            )
        )  # fmt: skip
    return out


def as_rows(targets: tuple[OpsTarget, ...]) -> list[Row]:
    return [
        (t.name, t.argv, t.runner, t.group, t.label, t.needs_stack, t.env, t.env_mode, t.confirm, t.confirm_when,
         t.after)
        for t in targets
    ]  # fmt: skip


def test_the_allowlist_is_the_table_in_doc_11() -> None:
    table = doc_table()
    assert len(table) == 29
    assert as_rows(TARGETS) == table
    assert sum(t.runner == "local" for t in TARGETS) == 11 and sum(t.runner == "aws" for t in TARGETS) == 18


def test_local_rows_are_make_name_env_local_scrubbed_with_nothing_else() -> None:
    for t in (t for t in TARGETS if t.runner == "local"):
        assert t.argv == ("make", t.name, "ENV=local"), t.name
        assert t.env_mode == "scrubbed" and t.confirm is None and t.after == () and not t.takes_cidr
        assert t.make_target not in LOCAL_NEVER and "ENV=aws" not in t.argv


def test_aws_rows_are_make_target_env_aws_dotenv() -> None:
    aws = [t for t in TARGETS if t.runner == "aws"]
    assert {t.make_target for t in aws} == T2 == AWS_MAKE_TARGETS
    for t in aws:
        assert t.argv[:3] == ("make", t.make_target, "ENV=aws") and t.env_mode == "dotenv", t.name
        extra = t.argv[3:]
        assert extra == (("SEED_ARGS=--bind-asish",) if t.name == "aws-seed-bind-asish" else ()), t.name
        assert t.takes_cidr == (t.name == "aws-admin-open")
        assert not any(a.startswith(("FORCE=", "ADMIN_CIDR=")) for a in t.argv)


def test_confirm_words_on_the_destructive_four_only() -> None:
    words = {t.name: (t.confirm, t.confirm_when) for t in TARGETS if t.confirm}
    assert words == {
        "aws-deploy": ("deploy", "plan_destroys"),
        "aws-admin-close": ("close", "always"),
        "aws-down": ("down", "always"),
        "aws-down-all": ("down-all", "always"),
    }
    assert {t.name for t in TARGETS if t.forces} == {"aws-admin-close", "aws-down", "aws-down-all"}


def test_every_after_names_an_earlier_aws_entry() -> None:
    seen: set[str] = set()
    for t in TARGETS:
        assert set(t.after) <= seen, t.name
        if t.runner == "aws":
            seen.add(t.name)


def test_no_never_target_and_no_eks() -> None:
    assert DOC_NEVER == NEVER
    for t in TARGETS:
        assert t.make_target not in NEVER and "ENV=eks" not in t.argv
    assert {t.make_target for t in TARGETS if t.runner == "local"} & LOCAL_NEVER == set()


def test_the_targets_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        TARGETS[0].argv = ("sh", "-c", "x")  # type: ignore[misc]
    assert isinstance(TARGETS, tuple) and all(isinstance(t.argv, tuple) for t in TARGETS)
    assert not hasattr(allowlist, "register") and not hasattr(allowlist, "add")


def test_no_env_set_lists_a_denied_key() -> None:
    for keys in ENV_SETS.values():
        assert [k for k in keys if DENY.match(k)] == []


@pytest.mark.parametrize(
    "bad", ["AWS_PROFILE", "MOCK_ADMIN_TOKEN", "TF_VAR_x", "ENV", "VIRTUAL_ENV", "API_KEY"]
)
def test_adding_a_denied_key_to_a_copy_raises(bad: str) -> None:
    doctored = {**ENV_SETS, "base": (*ENV_SETS["base"], bad)}
    with pytest.raises(AllowlistError, match="denied"):
        check(TARGETS, doctored)


def test_a_doctored_local_target_is_refused() -> None:
    t = TARGETS[0]
    for bad in (
        dataclasses.replace(t, argv=("make", "up", "ENV=aws")),
        dataclasses.replace(t, argv=("sh", "-c", "make up")),
        dataclasses.replace(t, argv=("make", "up", "ENV=local", "FORCE=1")),
        dataclasses.replace(t, name="push", argv=("make", "push", "ENV=local")),
        dataclasses.replace(t, name="test-nightly", argv=("make", "test-nightly", "ENV=local")),
        dataclasses.replace(t, env=("base", "aws")),
        dataclasses.replace(t, env_mode="dotenv"),
        dataclasses.replace(t, confirm="up", confirm_when="always"),
    ):
        with pytest.raises(AllowlistError):
            check((bad,), ENV_SETS)
    with pytest.raises(AllowlistError, match="duplicate"):
        check((t, t), ENV_SETS)


def test_a_doctored_aws_target_is_refused() -> None:
    names = by_name()
    plan, deploy, down = names["aws-plan"], names["aws-deploy"], names["aws-down"]
    prefix = (names["aws-ecr-up"], names["aws-push"])
    for bad in (
        dataclasses.replace(plan, env_mode="scrubbed"),  # an aws row with scrubbed
        dataclasses.replace(plan, argv=("make", "plan", "ENV=local")),
        dataclasses.replace(plan, argv=("make", "plan", "ENV=aws", "FORCE=1")),  # FORCE=1 in a table argv
        dataclasses.replace(plan, argv=("make", "plan", "ENV=aws", "ADMIN_CIDR=1.2.3.4/32")),
        dataclasses.replace(plan, argv=("make", "deploy-eks", "ENV=aws")),
        dataclasses.replace(plan, argv=("make", "plan", "ENV=eks")),
        dataclasses.replace(
            plan, confirm="plan", confirm_when="always"
        ),  # a confirm on a non-destructive row
        dataclasses.replace(plan, takes_cidr=True),
        dataclasses.replace(plan, after=("aws-test-report",)),  # a later entry
        dataclasses.replace(plan, name="plan2"),
        dataclasses.replace(plan, group="stack"),
    ):
        with pytest.raises(AllowlistError):
            check((*prefix, bad), ENV_SETS)
    for bad in (
        dataclasses.replace(deploy, confirm=None, confirm_when=None),
        dataclasses.replace(down, confirm="d"),
    ):
        with pytest.raises(AllowlistError, match="confirm"):
            check((*prefix, plan, bad), ENV_SETS)
    check(TARGETS, ENV_SETS)  # the real table passes


def test_child_env_drops_credentials_even_when_set_in_the_parent() -> None:
    parent = {
        "PATH": "/usr/bin", "HOME": "/home/me", "DOCKER_HOST": "unix:///run/docker.sock", "UV_CACHE_DIR": "/c",
        "AWS_PROFILE": "att", "AWS_ACCESS_KEY_ID": "AKIAABCDEFGHIJKLMNOP", "AWS_SECRET_ACCESS_KEY": "s3cr3t-value",
        "MOCK_ADMIN_TOKEN": "mt-value-xyz", "TF_VAR_x": "1", "ENV": "aws", "VIRTUAL_ENV": "/v", "TOWER_BEARER": "tb",
        "COGNITO_PASSWORD_ASISH": "pw", "RANDOM": "r",
    }  # fmt: skip
    for t in TARGETS:
        env = child_env(t, parent)
        assert not any(DENY.match(k) for k in env), t.name
        assert "RANDOM" not in env and env["PATH"] == "/usr/bin"
        assert {k: env[k] for k in FIXED_ENV} == dict(FIXED_ENV)
        assert ("DOCKER_HOST" in env) == ("docker" in t.env)
        assert ("UV_CACHE_DIR" in env) == ("uv" in t.env)


def test_mask_secrets() -> None:
    known = "k3y-of-the-ui-0123"
    text = (
        "curl -H 'Authorization: Bearer abc.def-ghi' x; FOO_TOKEN=xyz123 AWS_SECRET_ACCESS_KEY=wJalr/K7 "
        f'id AKIAIOSFODNN7EXAMPLE and ASIAIOSFODNN7EXAMPLE; "MOCK_ADMIN_TOKEN": "q1w2e3"; echo {known}; ok=1'
    )
    out = mask_secrets(text, [known, "short"])
    for secret in (
        "abc.def-ghi",
        "xyz123",
        "wJalr/K7",
        "AKIAIOSFODNN7EXAMPLE",
        "ASIAIOSFODNN7EXAMPLE",
        "q1w2e3",
        known,
    ):
        assert secret not in out, secret
    assert f"Bearer {HIDDEN}" in out and f"FOO_TOKEN={HIDDEN}" in out and "ok=1" in out
    assert (
        mask_secrets("short words stay", ["short"]) == "short words stay"
    )  # < 8 characters is never a secret
    assert re.search(r"MOCK_ADMIN_TOKEN\"?: \"?" + HIDDEN, out)


SECRET_40 = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
SESSION = "FwoGZXIvYXdzEXAMPLE" + "Ab1+/" * 30


@pytest.mark.parametrize(
    ("line", "gone", "expect"),
    [
        (f"aws_secret_access_key = {SECRET_40}", SECRET_40, f"aws_secret_access_key={HIDDEN}"),
        (f'"aws_session_token": "{SESSION}"', SESSION[:20], f"aws_session_token={HIDDEN}"),
        ("AWS_ACCESS_KEY_ID: 'AKIAIOSFODNN7EXAMPLE'", "AKIAIOSFODNN7EXAMPLE", f"AWS_ACCESS_KEY_ID={HIDDEN}"),
        (f"secret {SECRET_40} here", SECRET_40, f"secret {HIDDEN} here"),  # a bare secret-shaped run
        (f"token {SESSION} end", SESSION[:20], f"token {HIDDEN} end"),  # 100+ chars: a session token
    ],
)
def test_mask_secrets_aws_shapes(line: str, gone: str, expect: str) -> None:
    out = mask_secrets(line)
    assert gone not in out and expect in out, out


def test_a_git_sha_and_ordinary_words_survive() -> None:
    sha = "0f9e8d7c6b5a49382716f5e4d3c2b1a098765432"  # 40 hex: no upper case
    words = "Terraform will perform the following actions: module.tower.aws_lambda_function.alerts"
    assert mask_secrets(f"commit {sha}") == f"commit {sha}"
    assert mask_secrets(words) == words
    assert mask_secrets("A" * 40) == "A" * 40  # no lower case, no digit: not secret-shaped
