"""`make demo` as a subprocess against the running stack; its transcripts match the golden files
(tool calls + reason codes per utterance; wording is never compared — `ref_client.transcript.compare`)."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from ref_client.transcript import compare

from tests.e2e.helpers import GOLDEN, TRANSCRIPTS, Stack, make

pytestmark = pytest.mark.e2e


def _load(path: Any) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_make_demo_matches_golden(stack: Stack) -> None:
    started = time.time()
    r = make("demo", timeout=900)
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"make demo exited {r.returncode}:\n{out[-4000:]}"
    assert "demo ok: 4 stories" in out, out[-4000:]

    goldens = sorted(GOLDEN.glob("*.json"))
    assert {g.stem for g in goldens} == {"moment-1", "moment-2", "moment-3", "transplant"}
    diffs: list[str] = []
    for g in goldens:
        t = TRANSCRIPTS / g.name
        assert t.exists(), f"make demo wrote no {t}"
        assert t.stat().st_mtime >= started - 2, f"{t} was not rewritten by this run"
        transcript = _load(t)
        assert transcript["env"] == stack.env
        diffs += compare(_load(g), transcript)
    assert not diffs, "transcripts differ from golden:\n  " + "\n  ".join(diffs)


def test_make_demo_is_repeatable(stack: Stack) -> None:
    """A second run on the same stack (no `make seed` between) gives the same reason codes: the demo resets the
    mock's scenario and the grant itself — no manual step between runs."""
    r = make("demo", timeout=900)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
    for g in sorted(GOLDEN.glob("*.json")):
        assert not compare(_load(g), _load(TRANSCRIPTS / g.name))
