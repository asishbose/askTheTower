"""Transcripts: `utterance → tool call → structured result → spoken text` (09 §4), one JSON file per story in
`artifacts/transcripts/<name>.json`, redacted before write.

Also the two checks every transcript is held to:

- `invented_digits(spoken, summaries)` — 09 §5: the client may rephrase `summary`, never add a digit to it.
- `compare(golden, transcript)` — the end-to-end gate (testing-and-showcase §3): tool calls and reason codes
  must match the golden file; wording is not asserted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ref_client.privacy import REDACTED, mask, phone_spans

# The runtime copy of the strict privacy regex (00 conventions: `\+?\d{10,15}`). It must stay identical to
# tests/privacy/patterns.py `E164_STRICT` — test_transcripts.py asserts it; the image has no tests/ to import.
# The ref-client's gates use it *and* the shared rule (`privacy`, decision 1): the shared rule adds grouped
# `+` numbers; this one keeps refusing every bare 10-15 digit run, allowlisted dates included (stricter).
E164 = re.compile(r"\+?\d{10,15}")
DIGITS = re.compile(r"\d+")

DEFAULT_ARGS: dict[str, dict[str, Any]] = {
    "line_is_ok": {"line": "self"},
    "is_reachable": {"line": "self"},
    "watch_line": {"line": "self", "enable": None},
}


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)

    def dump(self) -> dict[str, Any]:
        return {"name": self.name, "args": dict(self.args)}


@dataclass
class Turn:
    """One utterance. `result` is the ToolResult the spoken answer rests on (the last call's), or None."""

    utterance: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    result: dict[str, Any] | None = None
    spoken: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    guard: dict[str, Any] | None = None  # set when the digit guard replaced the model's text

    @property
    def reason_codes(self) -> list[str]:
        return list(self.result["reason_codes"]) if self.result else []

    def dump(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "utterance": self.utterance,
            "tool_calls": [c.dump() for c in self.tool_calls],
            "result": self.result,
            "reason_codes": self.reason_codes,
            "spoken": self.spoken,
        }
        if len(self.results) > 1:
            out["all_results"] = self.results
        if self.guard:
            out["guard"] = self.guard
        return out


@dataclass
class Transcript:
    story: str
    title: str
    user: str
    agent: str
    model_id: str | None
    env: str
    steps: list[dict[str, Any]] = field(default_factory=list)

    def control(self, text: str) -> None:
        self.steps.append({"control": text})

    def note(self, text: str) -> None:
        self.steps.append({"note": text})

    def turn(self, turn: Turn) -> None:
        self.steps.append(turn.dump())

    def dump(self) -> dict[str, Any]:
        return {
            "story": self.story,
            "title": self.title,
            "user": self.user,
            "agent": self.agent,
            "model_id": self.model_id,
            "env": self.env,
            "steps": self.steps,
        }


# --- redaction --------------------------------------------------------------------------------------------
def redact(value: Any) -> Any:
    """Every string in the structure with phone-number hits (the shared rule, then the strict regex) replaced.
    Applied before anything is written."""
    if isinstance(value, str):
        return E164.sub(REDACTED, mask(value))
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def has_number(text: str) -> bool:
    """True if `text` holds a phone-number shape by the strict regex or by the shared rule."""
    return bool(E164.search(text) or phone_spans(text))


def write(transcript: Transcript, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{transcript.story}.json"
    path.write_text(
        json.dumps(redact(transcript.dump()), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


# --- 09 §5: no invented digits ------------------------------------------------------------------------------
def invented_digits(spoken: str, summaries: list[str]) -> list[str]:
    """Digit runs in `spoken` that appear in none of the `summaries` (as a digit run of their own)."""
    allowed = {d for s in summaries for d in DIGITS.findall(s)}
    return [d for d in DIGITS.findall(spoken) if d not in allowed]


def guard_spoken(spoken: str, results: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    """The client's own backstop for 09 §5: if the model's text carries a digit that no `summary` has, speak the
    summaries verbatim instead. Turns without a tool result are not guarded (nothing to contradict)."""
    if not results:
        return spoken, None
    summaries = [str(r.get("summary", "")) for r in results]
    bad = invented_digits(spoken, summaries)
    if not bad:
        return spoken, None
    return " ".join(summaries), {"invented_digits": bad, "action": "replaced_with_summary"}


# --- golden comparison --------------------------------------------------------------------------------------
def normalise_args(name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULT_ARGS.get(name, {}))
    for k, v in (args or {}).items():
        out[k] = v.strip().lower() if isinstance(v, str) else v
    if "line" in out and not out["line"]:
        out["line"] = "self"
    return out


def _turns(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in doc.get("steps", []) if "utterance" in s]


def compare(golden: dict[str, Any], transcript: dict[str, Any]) -> list[str]:
    """Differences between a golden file and a transcript: per utterance, in order, the tool calls (name and
    normalised args) and the reason codes. Empty list = match. Wording is never compared."""
    diffs: list[str] = []
    want, got = _turns(golden), _turns(transcript)
    if len(want) != len(got):
        diffs.append(f"{golden.get('story')}: {len(want)} utterances expected, {len(got)} in transcript")
    for i, (w, g) in enumerate(zip(want, got, strict=False)):
        where = f"{golden.get('story')} #{i + 1} {w['utterance']!r}"
        if w["utterance"] != g["utterance"]:
            diffs.append(f"{where}: transcript has utterance {g['utterance']!r}")
        wc = [(c["name"], normalise_args(c["name"], c.get("args"))) for c in w.get("tool_calls", [])]
        gc = [(c["name"], normalise_args(c["name"], c.get("args"))) for c in g.get("tool_calls", [])]
        if wc != gc:
            diffs.append(f"{where}: tool calls {gc} != golden {wc}")
        if list(w.get("reason_codes", [])) != list(g.get("reason_codes", [])):
            diffs.append(f"{where}: reason codes {g.get('reason_codes')} != golden {w.get('reason_codes')}")
    return diffs
