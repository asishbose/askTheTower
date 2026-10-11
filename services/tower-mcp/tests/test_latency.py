"""Latency gate (02 §4, testing-and-showcase §3): 200 calls per tool through `scripts/latency.py` against the
in-process mock; p95 < 400 ms on the live request paths; stored-state path reported separately.

That the stored path skips the carrier is asserted by counting carrier requests, not by comparing wall-clock
p50s (which flaked under load): a live answer fetches its fact set once — `line_is_ok` is one SIM Swap check plus
one Call Forwarding Signal call (no swap in the demo scenario, so no date call), `is_reachable` is one
Device Reachability Status call — and the stored-state path and `watch_line` make none. p50/p95 stay a report.

Writes the table to a temp file; set `TOWER_LATENCY_WRITE=1` to (re)write `artifacts/latency.md`
(the artefact is committed, so `make test` does not rewrite it on every run).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from tower_consent import Store

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("latency_script", ROOT / "scripts" / "latency.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


async def test_p95_under_gate(store: Store, tmp_path: Path, request: pytest.FixtureRequest) -> None:
    latency = _load()
    n = int(os.environ.get("LATENCY_N", "200"))
    rows = await latency.run_in_process(store, n)
    backend = request.node.callspec.params["store"]
    label = "DynamoDB Local (testcontainers)" if backend == "local" else "moto (in-process)"
    text = latency.render(
        rows, latency.environment(label, "in-process ASGI (Streamable HTTP)"), latency.LABEL_IN_PROCESS
    )
    out = (
        ROOT / "artifacts" / "latency.md"
        if os.environ.get("TOWER_LATENCY_WRITE") == "1"
        else tmp_path / "latency.md"
    )
    out.write_text(text, encoding="utf-8")
    assert "in-process, not representative" in text
    assert {r.path.split(" ")[0] for r in rows} == {"line_is_ok", "is_reachable", "watch_line"}
    assert all(len(r.samples) == n for r in rows)
    failing = [f"{r.path}: p95 {r.p95:.1f} ms" for r in rows if not r.passed]
    assert not failing, failing
    calls = {r.path: _by_api(r.carrier_calls) for r in rows}
    assert calls == {
        "line_is_ok — live (carrier)": {"sim-swap/check": n, "call-forwarding-signal/call-forwardings": n},
        "line_is_ok — stored state (Watch)": {},  # the stored path skips the carrier entirely
        "is_reachable — live (carrier)": {"device-reachability-status/retrieve": n},
        "watch_line — status": {},
    }
    assert "carrier requests counted: 2 per call" in text  # the counts are in the report too


def _by_api(calls: dict[str, int] | None) -> dict[str, int]:
    """`/sim-swap/v2/check` -> `sim-swap/check`: the API and operation, not the vendored version."""
    assert calls is not None, "in-process rows count carrier requests"
    out: dict[str, int] = {}
    for path, k in calls.items():
        parts = path.strip("/").split("/")
        key = f"{parts[0]}/{parts[-1]}"
        out[key] = out.get(key, 0) + k
    return out
