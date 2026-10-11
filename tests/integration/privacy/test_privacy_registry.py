"""Privacy gate (00 privacy invariants; testing-and-showcase §3): zero hits. The greps live with what they grep —
this registry asserts every one of them still exists, carries its layer marker, and imports the one regex module
(`tests/privacy/patterns.py`) rather than a pattern of its own."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
GREPS = {
    "tests/privacy/test_no_numbers.py": (
        "integration",
        "artifacts/ and captured logs: numbers, health words, keys",
    ),
    "services/tower-mcp/tests/test_privacy.py": ("integration", "tool results, Tower logs, audit rows"),
    "services/alerts/tests/test_privacy.py": (
        "integration",
        "SMS bodies and Alerts logs; no text to a swapped line",
    ),
    "services/binding-page/tests/test_privacy.py": ("integration", "binding page HTML, logs, tables"),
    "packages/tower-consent/tests/test_privacy.py": (
        "integration",
        "every consent table after a full exercise",
    ),
    "tests/e2e/test_privacy_logs.py": ("e2e", "the running stack's logs after the demo"),
    "tests/aws/test_terraform_static.py": ("unit", "no per-line metric label on any dashboard/metric filter"),
}


@pytest.mark.parametrize("path", sorted(GREPS))
def test_privacy_grep_exists_and_uses_the_one_module(path: str) -> None:
    layer, what = GREPS[path]
    text = (ROOT / path).read_text(encoding="utf-8")
    assert f"pytest.mark.{layer}" in text, f"{path} ({what}) is not marked {layer}"
    if path != "tests/aws/test_terraform_static.py":
        assert "tests.privacy.patterns import" in text or "tests.helpers.patterns import" in text, path
