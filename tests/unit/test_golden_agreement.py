"""Cross-package agreement for the one golden set (`tests/e2e/golden/`): every tool call names a tool Tower
registers (tower-mcp `descriptions`) with arguments it declares, every reason code is one the policy engine can
return (tower-policy `ENGINE_CODES`), and every story the reference client's demo runs has a golden file."""

from __future__ import annotations

import pytest
from ref_client.demo import STORIES as DEMO_STORIES
from tower_mcp.descriptions import ARGS, DESCRIPTIONS
from tower_policy import ReasonCode
from tower_policy.codes import ENGINE_CODES

from tests.helpers.patterns import HEALTH_WORDS, phone_hits
from tests.helpers.transcripts import GOLDEN, STORIES, load

pytestmark = pytest.mark.unit


def _turns(story: str) -> list[dict]:
    return [s for s in load(GOLDEN / f"{story}.json")["steps"] if "utterance" in s]


def test_golden_set_is_the_demo_stories() -> None:
    assert sorted(p.stem for p in GOLDEN.glob("*.json")) == sorted(STORIES)
    assert sorted(s.name for s in DEMO_STORIES) == sorted(STORIES)


@pytest.mark.parametrize("story", STORIES)
def test_tool_calls_and_codes_exist(story: str) -> None:
    turns = _turns(story)
    assert turns, f"{story}: no turns"
    for t in turns:
        for call in t.get("tool_calls", []):
            assert call["name"] in DESCRIPTIONS, (story, call)
            assert set(call.get("args", {})) <= set(ARGS[call["name"]]), (story, call)
        for code in t.get("reason_codes", []):
            assert ReasonCode(code) in ENGINE_CODES, (story, code)


@pytest.mark.parametrize("story", STORIES)
def test_golden_files_carry_no_numbers_or_health_words(story: str) -> None:
    text = (GOLDEN / f"{story}.json").read_text(encoding="utf-8")
    assert not phone_hits(text) and not HEALTH_WORDS.search(text)
