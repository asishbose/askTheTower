"""The transcript comparator and the one golden set.

`compare` is `ref_client.transcript.compare` — tool calls (name + arguments) and reason codes per utterance;
spoken wording and timestamps are never compared (prompt 18 guardrail: no test asserts model wording). The
golden files are hand-written from the policy table and used unchanged for ENV=local, eks and aws.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ref_client.transcript import compare

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "e2e" / "golden"
STORIES = ("moment-1", "moment-2", "moment-3", "transplant")

__all__ = ["GOLDEN", "STORIES", "compare", "compare_dir", "load"]


def load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def compare_dir(transcripts: Path, *, golden: Path = GOLDEN, since: float | None = None) -> list[str]:
    """Every golden story against `transcripts/<story>.json`. `since`: the file must be newer (this run)."""
    diffs: list[str] = []
    stems = sorted(p.stem for p in golden.glob("*.json"))
    if tuple(stems) != tuple(sorted(STORIES)):
        return [f"golden set is {stems}, expected {sorted(STORIES)}"]
    for stem in stems:
        t = transcripts / f"{stem}.json"
        if not t.exists():
            diffs.append(f"{stem}: no transcript at {t}")
            continue
        if since is not None and t.stat().st_mtime < since - 2:
            diffs.append(f"{stem}: {t} was not rewritten by this run")
            continue
        diffs += compare(load(golden / f"{stem}.json"), load(t))
    return diffs
