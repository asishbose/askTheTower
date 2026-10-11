"""Chaos (testing-and-showcase §3): for each mock fault × each tool, against the running ENV, the answer is a
refusal with the mapped code — STALE_DATA or CARRIER_ERROR — and never an outcome about the line (OK, changed).

`watch_line` makes no carrier check (02 §2), so a fault must leave it exactly as it was and unconsumed: the right
outcome there is the unfaulted one. Runs last in the e2e session (tests/e2e/conftest.py) and heals the stack
(faults cleared, circuit breaker closed) before it hands back. The Lambda-cold-start variant is nightly-only.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tests.e2e.helpers import make
from tests.e2e.tower import call
from tests.helpers.env import MockAdmin, Stack, Targets

pytestmark = pytest.mark.e2e

FAULTS = ("timeout", "500", "429")
CARRIER_TOOLS = ("line_is_ok", "is_reachable")
TOOLS = (*CARRIER_TOOLS, "watch_line")
USER = "user-asish"  # the demo seed's line holder
REFUSALS = {"STALE_DATA", "CARRIER_ERROR"}
LINE_OUTCOMES = {"OK", "SIM_SWAPPED_RECENT", "CALL_FORWARDING_SET", "UNREACHABLE"}
CARRIER_FACTS = {
    "line_is_ok": ("sim_swapped_recently", "swapped_at", "call_forwarding"),
    "is_reachable": ("reachable", "connectivity", "last_status_time"),
}
BREAKER_HEAL_S = 75.0  # camara_client.breaker: open for 60 s after five 5xx in a row
results: list[tuple[str, str, list[str]]] = []


def _healthy(env: Targets) -> None:
    deadline = time.monotonic() + BREAKER_HEAL_S
    while True:
        r = call(env, USER, "line_is_ok", {"line": "self"})
        if not REFUSALS.intersection(r["reason_codes"]):
            return
        if time.monotonic() > deadline:
            pytest.fail(f"stack did not heal within {BREAKER_HEAL_S:.0f} s: {r['reason_codes']}")
        time.sleep(2)


@pytest.fixture(scope="module")
def chaos(stack: Stack, env: Targets, mock_admin: MockAdmin) -> Any:
    mock_admin.clear_faults()
    if env.env == "local":
        # The demo and showcase leave Watches behind; a fresh Watch answers line_is_ok from stored state with no
        # carrier call, which would test nothing here. `make seed` clears them and reloads the demo scenario.
        r = make("seed", timeout=300)
        assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]
    else:
        mock_admin.load_scenario("demo")
    _healthy(env)
    yield mock_admin
    mock_admin.clear_faults()
    mock_admin.load_scenario("demo")
    _healthy(env)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("kind", FAULTS)
def test_fault_never_gives_a_wrong_outcome(chaos: MockAdmin, env: Targets, kind: str, tool: str) -> None:
    _healthy(env)
    before = call(env, USER, tool, {"line": "self"})
    chaos.inject_fault(kind, 3)  # more than one tool call makes (line_is_ok: two carrier calls in parallel)
    try:
        r = call(env, USER, tool, {"line": "self"})
        left = len(chaos.state()["faults"])
    finally:
        chaos.clear_faults()
    results.append((kind, tool, list(r["reason_codes"])))

    if tool in CARRIER_TOOLS:
        assert left < 3, f"{tool} made no carrier call under fault {kind}"
        assert set(r["reason_codes"]) <= REFUSALS, f"{kind} × {tool}: {r['reason_codes']} is not a refusal"
        assert not LINE_OUTCOMES.intersection(r["reason_codes"])
        if "STALE_DATA" not in r["reason_codes"]:  # no last-known facts → nothing about the line is claimed
            claimed = {f: r["facts"].get(f) for f in CARRIER_FACTS[tool] if r["facts"].get(f) is not None}
            assert not claimed, f"{kind} × {tool} refused but still claims {claimed}"
        assert r["next_step"]["kind"] in ("none", "call_carrier")
    else:
        assert left == 3, "watch_line consumed a carrier fault: it must make no carrier check"
        assert r["reason_codes"] == before["reason_codes"]
        assert r["facts"]["watching"] == before["facts"]["watching"]


def test_matrix_was_complete() -> None:
    """Runs after the parametrised cases (same module, defined after them): all 9 cells ran, none wrong."""
    cells = {(k, t) for k, t, _ in results}
    if not cells:
        pytest.skip("no chaos cell ran (stack not running)")
    assert cells == {(k, t) for k in FAULTS for t in TOOLS}
