"""09 §5: the client may read `summary` verbatim or rephrase it, never add a fact — checked as "no digit in the
spoken text that is not in `summary`", for every ToolResult the golden demo run produces.

Three layers: the checker itself; the agent's own guard (a model reply with an invented digit is replaced by the
summary — exercised with a fake Converse client); and the live model on the same ToolResults (skipped without AWS
credentials).
"""

from __future__ import annotations

from typing import Any

import pytest
from ref_client.agent import BedrockAgent
from ref_client.transcript import guard_spoken, invented_digits

from .conftest import RefStack
from .fakes import FakeBedrock, FakeTower
from .helpers import needs_bedrock, scripted_demo

SWAP = {
    "summary": "Your SIM was moved to another device at 10:12 today. If that wasn't you, call your carrier now.",
    "facts": {"line": "self", "sim_swapped_recently": True, "swapped_at": "2026-10-05T14:12:00Z"},
    "reason_codes": ["SIM_SWAPPED_RECENT"],
    "next_step": {"kind": "call_carrier", "url": None, "carrier_support_number": "611"},
    "checked_at": "2026-10-05T14:12:00Z",
}


async def _golden_results(ref_stack: RefStack, agent_for: Any) -> list[tuple[dict[str, Any], str]]:
    run = await scripted_demo(agent_for, ref_stack.control, None)
    return [(s["result"], s["spoken"]) for t in run.transcripts for s in t.dump()["steps"] if s.get("result")]


@pytest.mark.unit
def test_checker() -> None:
    s = [SWAP["summary"]]
    assert invented_digits("Your SIM moved to another device at 10:12 today; call your carrier.", s) == []
    assert invented_digits("Your SIM moved at 10:15 today.", s) == ["15"]
    assert invented_digits("It moved 12 minutes ago.", s) == []  # "12" is in the summary
    assert invented_digits("Call 611 now.", s) == ["611"]  # next_step's number is not in summary: not allowed
    assert invented_digits("Your line is fine.", ["Your line is as it was."]) == []


@pytest.mark.unit
def test_guard_replaces_an_invented_digit() -> None:
    text, guard = guard_spoken("Your SIM moved at 2:14 today.", [SWAP])
    assert text == SWAP["summary"] and guard == {
        "invented_digits": ["2", "14"],
        "action": "replaced_with_summary",
    }
    assert guard_spoken("Someone moved your SIM at 10:12 today.", [SWAP]) == (
        "Someone moved your SIM at 10:12 today.",
        None,
    )
    assert guard_spoken("It's 12 degrees.", []) == (
        "It's 12 degrees.",
        None,
    )  # no tool result: nothing to guard


@pytest.mark.unit
async def test_agent_applies_the_guard() -> None:
    tower = FakeTower(SWAP)
    fake = FakeBedrock(
        [("tool", "line_is_ok", {"line": "self"}), ("text", "Your SIM moved at 10:14, call 611.")]
    )
    turn = await BedrockAgent(tower, client=fake, system_prompt="sys").ask("is my line ok")
    assert [c.name for c in turn.tool_calls] == ["line_is_ok"] and tower.calls == [
        ("line_is_ok", {"line": "self"})
    ]
    assert turn.spoken == SWAP["summary"]
    assert turn.guard and turn.guard["invented_digits"] == ["14", "611"]
    # The request carried Tower's descriptions verbatim and temperature 0.
    req = fake.requests[0]
    assert req["inferenceConfig"]["temperature"] == 0.0
    from tower_mcp.descriptions import DESCRIPTIONS

    assert {
        t["toolSpec"]["name"]: t["toolSpec"]["description"] for t in req["toolConfig"]["tools"]
    } == DESCRIPTIONS
    # The tool result went back to the model as JSON.
    assert fake.requests[1]["messages"][2]["content"][0]["toolResult"]["content"] == [{"json": SWAP}]


@pytest.mark.integration
async def test_golden_demo_results_spoken_without_invented_digits(
    ref_stack: RefStack, scripted_agent_for: Any
) -> None:
    pairs = await _golden_results(ref_stack, scripted_agent_for)
    assert len(pairs) == 9
    assert any(any(ch.isdigit() for ch in r["summary"]) for r, _ in pairs)  # the check is not vacuous
    for result, spoken in pairs:
        assert invented_digits(spoken, [result["summary"]]) == [], (spoken, result["summary"])


@pytest.mark.e2e
@needs_bedrock
async def test_model_adds_no_digits(ref_stack: RefStack, scripted_agent_for: Any) -> None:
    """The live model, before the guard: for each golden ToolResult, its own words carry no new digit."""
    pairs = await _golden_results(ref_stack, scripted_agent_for)
    for result, _ in pairs:
        turn = await BedrockAgent(FakeTower(result)).ask("is my line ok")
        assert turn.guard is None, (turn.guard, result["summary"])
