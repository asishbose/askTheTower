"""09 §6.2 rule 1 (`auth.bearer_header`) and the scripted agent's exact-phrase lookup (`phrasebook`)."""

from __future__ import annotations

import pytest
from ref_client import phrasebook
from ref_client.auth import MAX_HEADER_LEN, bearer_header
from ref_client.demo import STORIES, Say
from ref_client.transcript import ToolCall

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "value",
    ["Bearer abc.def-ghi_jkl~mno+pqr/stu=", "bearer abc", "BEARER abc==", "Bearer " + "a" * 2000],
)
def test_well_formed_bearer_passes_unchanged(value: str) -> None:
    assert bearer_header(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer  abc",
        "Basic abc",
        "Bearer a b",
        "Bearer abc\n",
        "Token abc",
        " Bearer abc",
    ],
)
def test_missing_or_malformed_bearer_is_refused(value: str | None) -> None:
    assert bearer_header(value) is None


def test_oversized_header_is_refused() -> None:
    assert bearer_header("Bearer " + "a" * MAX_HEADER_LEN) is None


def test_normalise() -> None:
    assert phrasebook.normalise("  Is my line OK?! ") == "is my line ok"
    assert phrasebook.normalise("Is Mom’s line OK?") == "is mom's line ok"


def test_every_demo_line_is_in_the_phrasebook() -> None:
    table = phrasebook.build()
    for story in STORIES:
        for step in story.steps:
            if isinstance(step, Say):
                assert table[phrasebook.normalise(step.text)] == ToolCall(
                    step.expect.name, dict(step.expect.args)
                )
    assert table["is my line ok"] == ToolCall("line_is_ok", {"line": "self"})
    assert "is the line ok" not in table  # a clarify entry names no tool: the scripted agent calls none
