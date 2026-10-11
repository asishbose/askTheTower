"""Shared constants and the `make` runner for the e2e tests. Endpoints come from `tests.helpers.env.resolve()`
(ENV=local|eks|aws); the golden set is `tests/e2e/golden/` (one set for all three ENVs)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.helpers.env import Stack, resolve
from tests.helpers.transcripts import GOLDEN

ROOT = Path(__file__).resolve().parents[2]
_TARGETS = resolve()
ENV = _TARGETS.env
TOWER_URL = _TARGETS.tower_url
MOCK_URL = _TARGETS.mock_url
BINDING_URL = _TARGETS.binding_url
LOGS = ROOT / "deploy" / "compose" / "logs"
TRANSCRIPTS = ROOT / "artifacts" / "transcripts"

__all__ = [
    "BINDING_URL",
    "ENV",
    "GOLDEN",
    "LOGS",
    "MOCK_URL",
    "ROOT",
    "TOWER_URL",
    "TRANSCRIPTS",
    "Stack",
    "make",
]


def make(*args: str, timeout: float = 900.0) -> subprocess.CompletedProcess[str]:
    """`make <args>` from the repo root, output captured (and echoed on failure by the callers' asserts)."""
    return subprocess.run(  # noqa: S603
        ["make", "--no-print-directory", *args],  # noqa: S607
        cwd=ROOT,
        env=os.environ | {"ENV": ENV},
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        check=False,
    )
