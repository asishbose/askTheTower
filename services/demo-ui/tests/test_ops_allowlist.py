"""Doc 11 §2 "Pane 5", §11: the allowlist is the doc's table, every argv is `make <name> ENV=local`, no O2 name, no
denied key in an env set (and adding one fails at import), the child env is an allow-list, and `mask_secrets`."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
from demo_ui.ops import allowlist
from demo_ui.ops.allowlist import (
    DENY,
    ENV_SETS,
    FIXED_ENV,
    NEVER,
    TARGETS,
    AllowlistError,
    OpsTarget,
    check,
    child_env,
)
from demo_ui.redact import HIDDEN, mask_secrets

pytestmark = pytest.mark.unit

DOC = Path(__file__).resolve().parents[3] / "docs" / "architecture" / "components" / "11-demo-ui.md"
O2 = {"ecr-up", "push", "plan", "deploy", "down-eks", "down-all", "cognito-users", "seed-aws", "web-chat-sync",
      "register-gateway"}  # fmt: skip


def doc_table() -> list[tuple[str, tuple[str, ...], str, str, bool, tuple[str, ...]]]:
    text = DOC.read_text(encoding="utf-8")
    block = text.split("<!-- ops-allowlist:start -->", 1)[1].split("<!-- ops-allowlist:end -->", 1)[0]
    rows = [r for r in block.strip().splitlines() if r.startswith("|")][2:]  # header and rule
    out = []
    for row in rows:
        name, argv, group, label, needs, env = (c.strip().strip("`") for c in row.strip("|").split("|"))
        out.append(
            (
                name,
                tuple(argv.split()),
                group,
                label,
                needs == "yes",
                tuple(e.strip() for e in env.split(",")),
            )
        )
    return out


def as_rows(
    targets: tuple[OpsTarget, ...],
) -> list[tuple[str, tuple[str, ...], str, str, bool, tuple[str, ...]]]:
    return [(t.name, t.argv, t.group, t.label, t.needs_stack, t.env) for t in targets]


def test_the_allowlist_is_the_table_in_doc_11() -> None:
    table = doc_table()
    assert len(table) == 11
    assert as_rows(TARGETS) == table


def test_every_argv_is_make_name_env_local() -> None:
    for t in TARGETS:
        assert t.argv[0] == "make" and t.argv[1] == t.name
        assignments = [a for a in t.argv if "=" in a]
        assert assignments == ["ENV=local"], t.name
        assert len(t.argv) == 3


def test_no_never_target_is_allowed() -> None:
    names = {t.name for t in TARGETS}
    assert names & NEVER == set() and names & O2 == set()
    assert O2 <= NEVER
    for t in TARGETS:
        assert not any(a in {"ENV=aws", "ENV=eks"} for a in t.argv)


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


def test_a_doctored_target_is_refused() -> None:
    t = TARGETS[0]
    for bad in (
        dataclasses.replace(t, argv=("make", "up", "ENV=aws")),
        dataclasses.replace(t, argv=("sh", "-c", "make up")),
        dataclasses.replace(t, argv=("make", "up", "ENV=local", "FORCE=1")),
        dataclasses.replace(t, name="push", argv=("make", "push", "ENV=local")),
        dataclasses.replace(t, env=("base", "aws")),
    ):
        with pytest.raises(AllowlistError):
            check((bad,), ENV_SETS)
    with pytest.raises(AllowlistError, match="duplicate"):
        check((t, t), ENV_SETS)


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
