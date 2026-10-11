"""Test fixtures shared across layers (prompt 18). Data, not code under test.

- `SCENARIOS` — the mock carrier's scenario files. Not a copy and not a symlink (symlinks do not survive a
  Windows checkout): the path of `scenarios/` itself, so a test and the mock always read the same YAML.
- `CLOUDEVENTS` — one sample per CAMARA event type Alerts consumes (`cloudevents/*.json`), no phone numbers;
  `tests/unit/test_fixtures.py` validates each against the vendored CloudEvents schemas Alerts uses.
- `FakeGateway` — the in-process AgentCore Gateway stand-in (OpenAPI → MCP tools over the mock), owned by
  `camara_client.testing` and re-exported here so cross-package tests have one place to import it from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from camara_client.testing import FakeGateway

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "scenarios"
CLOUDEVENTS = Path(__file__).resolve().parent / "cloudevents"

__all__ = ["CLOUDEVENTS", "SCENARIOS", "FakeGateway", "cloudevent", "cloudevents", "scenario"]


def scenario(name: str) -> Path:
    """`scenarios/<name>.yaml`; KeyError if the mock has no such scenario."""
    path = SCENARIOS / f"{name}.yaml"
    if not path.exists():
        raise KeyError(f"no scenario {name!r} in {SCENARIOS}")
    return path


def cloudevent(name: str) -> dict[str, Any]:
    """A fresh copy of `cloudevents/<name>.json`, e.g. `cloudevent("sim-swap.swapped")`."""
    return dict(json.loads((CLOUDEVENTS / f"{name}.json").read_text(encoding="utf-8")))


def cloudevents() -> dict[str, dict[str, Any]]:
    return {p.stem: cloudevent(p.stem) for p in sorted(CLOUDEVENTS.glob("*.json"))}
