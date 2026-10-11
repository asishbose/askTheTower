"""`demo` vs the golden transcripts (testing-and-showcase §3, end-to-end row): tool calls and reason codes must match;
wording is not asserted. Golden files are hand-written from artifacts/policy-table.md (RUN-ALL step 10).

The in-process run uses the scripted agent (no Bedrock here); the Bedrock run of the same check is skipped
without AWS credentials. `REF_TRANSCRIPTS_WRITE=1` writes artifacts/transcripts/<story>.json.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from ref_client.agent import BedrockAgent
from ref_client.demo import STORIES, run_demo
from ref_client.transcript import E164, compare, redact

from tests.privacy.patterns import E164_STRICT, HEALTH_WORDS, phone_hits

from .conftest import RefStack
from .helpers import STORIES as STORY_NAMES
from .helpers import golden, needs_bedrock, scripted_demo, transcripts_dir


@pytest.mark.integration
async def test_scripted_demo_matches_golden(
    ref_stack: RefStack, scripted_agent_for: Any, tmp_path: Path
) -> None:
    out = transcripts_dir(tmp_path)
    run = await scripted_demo(scripted_agent_for, ref_stack.control, out)
    assert run.mismatches == []
    assert [t.story for t in run.transcripts] == list(STORY_NAMES)
    for name in STORY_NAMES:
        written = json.loads((out / f"{name}.json").read_text(encoding="utf-8"))
        assert compare(golden(name), written) == [], name
        text = json.dumps(written, ensure_ascii=False)
        assert phone_hits(text) == [], name
        assert not HEALTH_WORDS.search(" ".join(s.get("spoken", "") for s in written["steps"])), name
        assert written["agent"] == "scripted"
    # The watch in moment 3 / transplant reached Alerts' (stub) subscribe call, with ids only.
    assert [c["enable"] for c in ref_stack.alerts.calls] == [True, True]


@pytest.mark.unit
def test_golden_files_are_marked_and_agree_with_the_script() -> None:
    for story in STORIES:
        g = golden(story.name)
        assert g["GOLDEN"] == "hand-written"
        want = [tuple(s["reason_codes"]) for s in g["steps"] if "utterance" in s]
        script = [s.codes for s in story.steps if hasattr(s, "codes")]
        assert want == script, story.name


@pytest.mark.unit
def test_compare_flags_tool_and_code_drift() -> None:
    g = golden("moment-1")
    same = copy.deepcopy(g)
    assert compare(g, same) == []
    # Defaults and case do not matter.
    same["steps"][1]["tool_calls"][0]["args"] = {"line": " SELF "}
    assert compare(g, same) == []
    wrong_tool = copy.deepcopy(g)
    wrong_tool["steps"][1]["tool_calls"][0]["name"] = "is_reachable"
    assert any("tool calls" in d for d in compare(g, wrong_tool))
    wrong_arg = copy.deepcopy(g)
    wrong_arg["steps"][3]["tool_calls"][0]["args"] = {"line": "mom"}
    assert any("tool calls" in d for d in compare(g, wrong_arg))
    wrong_code = copy.deepcopy(g)
    wrong_code["steps"][3]["reason_codes"] = ["OK"]
    assert any("reason codes" in d for d in compare(g, wrong_code))
    short = copy.deepcopy(g)
    short["steps"] = short["steps"][:2]
    assert any("utterances expected" in d for d in compare(g, short))


@pytest.mark.unit
def test_redaction_uses_the_privacy_regex() -> None:
    assert E164.pattern == E164_STRICT.pattern  # tests/privacy/patterns.py is the source of truth
    doc: dict[str, Any] = {
        "a": "call +16135550101 now",
        "b": ["16135550102"],
        "c": {"d": "10:12 today"},
        "n": 3,
    }
    out = redact(doc)
    assert phone_hits(json.dumps(out)) == []
    assert out["c"]["d"] == "10:12 today" and out["n"] == 3


@pytest.mark.e2e
@needs_bedrock
async def test_bedrock_demo_matches_golden(ref_stack: RefStack, tmp_path: Path) -> None:
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as stack:

        async def agent_for(user_id: str) -> BedrockAgent:
            return BedrockAgent(await stack.enter_async_context(ref_stack.tower(user_id)))

        run = await run_demo(agent_for, ref_stack.control, out_dir=tmp_path, echo=lambda _l: None)
    for t in run.transcripts:
        assert compare(golden(t.story), json.loads((tmp_path / f"{t.story}.json").read_text())) == [], t.story
