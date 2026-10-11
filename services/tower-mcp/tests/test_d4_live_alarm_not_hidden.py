"""D4 (code-vs-docs.md), the other half of the rule: a half answer never hides a known alarm.

The SIM swap check times out, but call forwarding answers "unconditional". That is a change the carrier did
report, so the answer is CHANGED [CALL_FORWARDING_SET] — even when a Watch with older, quiet facts exists
(the stale fallback of 02 §6 applies only when the live answer would be refused).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from camara_client import CarrierError, CFResult
from tower_audit.reader import query_rows
from tower_consent import LastState
from tower_mcp.seed import seed_watch
from tower_policy import ReasonCode

from .conftest import MOCK_START, Stack

pytestmark = pytest.mark.integration


def _swap_times_out_forwarding_set(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    async def timeout(*_: Any, **__: Any) -> Any:
        raise CarrierError(ReasonCode.STALE_DATA, True, kind="timeout")

    async def unconditional(*_: Any, **__: Any) -> CFResult:
        return CFResult(status="unconditional")

    monkeypatch.setattr(stack.deps.carrier, "sim_swap_check", timeout)
    monkeypatch.setattr(stack.deps.carrier, "call_forwarding", unconditional)


@pytest.mark.parametrize("with_watch", [False, True])
async def test_d4_swap_timeout_with_forwarding_set_is_changed(
    stack: Stack, monkeypatch: pytest.MonkeyPatch, with_watch: bool
) -> None:
    if with_watch:
        quiet = LastState(cf_status="none", at=MOCK_START - timedelta(minutes=30))
        seed_watch(stack.store, stack.seed.asish_line, "user-asish", quiet)
    _swap_times_out_forwarding_set(stack, monkeypatch)
    r = await stack.line_is_ok("user-asish")
    assert r.reason_codes == ["CALL_FORWARDING_SET"]
    assert r.facts.source == "carrier" and r.facts.call_forwarding == "unconditional"
    assert r.facts.sim_swapped_recently is None  # unknown stays unknown, never "no"
    row = query_rows(stack.store, stack.seed.asish_line)[-1]
    assert (row.outcome, row.reason_codes) == ("changed", ("CALL_FORWARDING_SET",))
