"""The phrasing corpus (09 §3): its shape, the scoring, that the model sees Tower's descriptions verbatim, and —
with AWS credentials — the live run against Tower + mock, gated at CORPUS_MIN_PASS (default 0.9), writing
artifacts/corpus.md. Without credentials the live run is skipped, not failed (RUN-ALL Decisions).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from ref_client.agent import BedrockAgent, converse_tools
from ref_client.corpus_runner import CorpusReport, expectations, load_corpus, matches, min_pass, run_corpus
from ref_client.transcript import ToolCall, Turn
from tower_mcp.descriptions import ARGS, DESCRIPTIONS

from tests.privacy.patterns import HEALTH_WORDS, phone_hits

from .conftest import RefStack
from .helpers import ROOT, needs_bedrock


@pytest.mark.unit
def test_corpus_shape() -> None:
    corpus = load_corpus()
    assert len(corpus) >= 40
    tags = Counter(t for e in corpus for t in e.get("tags", []))
    for tool in DESCRIPTIONS:
        assert tags[tool] >= 9, tool  # ~10 per tool
    assert tags["off-topic"] >= 5 and tags["ambiguous"] >= 3 and tags["wrong-language"] >= 2
    assert tags["slang"] >= 3 and "did my sim get jacked" in {e["say"] for e in corpus}
    assert len({e["say"] for e in corpus}) == len(corpus), "duplicate phrasing"
    for e in corpus:
        assert ("expect" in e) != ("expect_any" in e), e
        assert not phone_hits(e["say"]) and not HEALTH_WORDS.search(e["say"]), e["say"]
        assert not any(ch.isdigit() for ch in e["say"]), e["say"]
        for x in expectations(e):
            if x.get("clarify"):
                assert set(x) == {"clarify"}
                continue
            if x["tool"] is None:
                continue
            assert x["tool"] in DESCRIPTIONS, e
            assert set(x.get("args", {})) <= set(ARGS[x["tool"]]), e
        if "expect" in e and e["expect"].get("tool"):
            assert e["expect"]["tool"] in e["tags"], e["say"]  # tags say which tool section it belongs to
        # Entries are written from people's words, not the tool names.
        assert not any(name in e["say"] for name in DESCRIPTIONS), e["say"]


class CannedAgent:
    name = "canned"
    model_id = "canned-model"

    def __init__(self, answers: dict[str, Turn]) -> None:
        self.answers = answers

    async def ask(self, utterance: str, *, expect: ToolCall | None = None) -> Turn:
        return self.answers[utterance]


def _turn(say: str, name: str | None = None, spoken: str = "ok", **args: Any) -> Turn:
    return Turn(say, [ToolCall(name, args)] if name else [], spoken=spoken)


@pytest.mark.unit
async def test_scoring_and_report() -> None:
    entries = [
        {"say": "a", "expect": {"tool": "line_is_ok", "args": {"line": "self"}}},
        {"say": "b", "expect": {"tool": "watch_line", "args": {"line": "mom", "enable": None}}},
        {"say": "c", "expect": {"tool": None}},
        {"say": "d", "expect": {"clarify": True}},
        {"say": "e", "expect_any": [{"tool": "is_reachable", "args": {"line": "mom"}}, {"tool": None}]},
    ]
    agent = CannedAgent(
        {
            "a": _turn("a", "line_is_ok"),  # default line = self
            "b": _turn("b", "watch_line", line="Mom"),  # enable omitted = status
            "c": _turn("c", spoken="It's sunny."),
            "d": _turn("d", spoken="Which line do you mean — yours or Mom's?"),
            "e": _turn("e", "line_is_ok", line="mom"),  # wrong tool: a miss
        }
    )
    report = await run_corpus(agent, entries, threshold=0.9)
    assert [r.ok for r in report.rows] == [True, True, True, True, False]
    assert report.rate == 0.8 and not report.ok
    md = report.markdown()
    assert "4/5 = 80%" in md and "FAIL" in md and "line_is_ok(line=mom)" in md and "## Misses" in md
    assert (await run_corpus(agent, entries[:4], threshold=0.9)).ok


@pytest.mark.unit
def test_matching_edges() -> None:
    clarify = {"clarify": True}
    assert not matches(clarify, _turn("x", spoken="Sure, checking."))
    assert not matches(clarify, _turn("x", "line_is_ok", spoken="Which line?"))
    assert matches({"tool": None}, _turn("x", spoken="Which line?"))
    want_on = {"tool": "watch_line", "args": {"line": "mom", "enable": True}}
    assert not matches(want_on, _turn("x", "watch_line", line="mom", enable=False))
    assert matches(want_on, _turn("x", "watch_line", line="mom", enable=True))


@pytest.mark.unit
def test_min_pass_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORPUS_MIN_PASS", raising=False)
    assert min_pass() == 0.9
    monkeypatch.setenv("CORPUS_MIN_PASS", "0.95")
    assert min_pass() == 0.95


@pytest.mark.integration
async def test_model_sees_towers_descriptions_verbatim(ref_stack: RefStack) -> None:
    async with ref_stack.tower() as tower:
        tools = await tower.tools()
    assert {t.name: t.description for t in tools} == DESCRIPTIONS
    specs = {t["toolSpec"]["name"]: t["toolSpec"] for t in converse_tools(tools)}
    assert {n: s["description"] for n, s in specs.items()} == DESCRIPTIONS
    watch = specs["watch_line"]["inputSchema"]["json"]["properties"]
    assert watch["enable"]["type"] == "boolean" and watch["line"]["default"] == "self"
    # Every argument the corpus expects exists in the schema Tower advertises.
    for e in load_corpus():
        for x in expectations(e):
            if x.get("tool"):
                assert set(x.get("args", {})) <= set(specs[x["tool"]]["inputSchema"]["json"]["properties"])


@pytest.mark.e2e
@needs_bedrock
async def test_corpus_against_tower(ref_stack: RefStack) -> None:
    async with ref_stack.tower() as tower:
        report: CorpusReport = await run_corpus(BedrockAgent(tower))
    (ROOT / "artifacts" / "corpus.md").write_text(report.markdown(), encoding="utf-8")
    assert report.ok, report.markdown()
