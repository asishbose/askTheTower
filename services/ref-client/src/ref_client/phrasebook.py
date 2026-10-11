"""The scripted agent's lookup for free text (the web chat endpoint without Bedrock, 09 §6.3).

`ScriptedAgent` needs the tool call named for it. In the demo the script names it; behind `POST /invocations` the
utterance is looked up here: the demo stories' lines (`demo.STORIES`) and the corpus phrasings with one expected
tool (09 §3), matched after lower-casing and dropping punctuation. It is an exact-phrase table, not a model and
not a classifier: an unknown sentence gets no tool call and the scripted agent says so.
"""

from __future__ import annotations

import re
from pathlib import Path

from ref_client.corpus_runner import load_corpus
from ref_client.demo import STORIES, Say
from ref_client.transcript import ToolCall

_PUNCT = re.compile(r"[^a-z0-9' ]+")
_SPACE = re.compile(r"\s+")


def normalise(utterance: str) -> str:
    return _SPACE.sub(" ", _PUNCT.sub(" ", utterance.lower().replace("’", "'"))).strip()


def build(corpus: Path | None = None) -> dict[str, ToolCall]:
    """normalised utterance → the tool call. Demo lines win over corpus lines on a clash."""
    table: dict[str, ToolCall] = {}
    for entry in load_corpus(corpus):
        expect = entry.get("expect") or {}
        if expect.get("tool"):
            table[normalise(str(entry["say"]))] = ToolCall(
                str(expect["tool"]), dict(expect.get("args") or {})
            )
    for story in STORIES:
        for step in story.steps:
            if isinstance(step, Say):
                table[normalise(step.text)] = ToolCall(step.expect.name, dict(step.expect.args))
    return table
