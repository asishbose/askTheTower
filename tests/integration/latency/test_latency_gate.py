"""Latency gate (testing-and-showcase §3): p95 < 400 ms on the request path. Measured by
services/tower-mcp/tests/test_latency.py (in-process, 200 calls per tool, `integration`) and `scripts/latency.py`
(`artifacts/latency.md`, and `latency-compose.md` against the compose stack). This module checks every gated row
of every latency table that exists: a stale or hand-edited table cannot carry a p95 over the gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.reports import ARTIFACTS, P95_GATE_MS, read_latency

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
TABLES = ["latency.md", "latency-compose.md", "latency-aws.md"]


def test_the_measuring_test_is_integration() -> None:
    text = (ROOT / "services/tower-mcp/tests/test_latency.py").read_text(encoding="utf-8")
    assert "pytest.mark.integration" in text and "400" in text


@pytest.mark.parametrize("name", TABLES)
def test_gated_rows_under_400ms(name: str) -> None:
    path = ARTIFACTS / name
    if not path.exists():
        pytest.skip(f"{name} not produced in this environment")
    rows = read_latency(path)
    if not rows and "not measured" in path.read_text(encoding="utf-8").lower():
        pytest.skip(f"{name} is a placeholder (not measured here)")
    assert rows, f"{name}: no latency rows"
    gated = [r for r in rows if r.gated]
    assert gated, f"{name}: no gated row"
    over = [
        f"{r.path}: p95 {r.p95} ms ({r.verdict})"
        for r in gated
        if r.p95 >= P95_GATE_MS or r.verdict != "pass"
    ]
    assert not over, f"{name}: p95 gate ({P95_GATE_MS:.0f} ms) missed: {over}"
