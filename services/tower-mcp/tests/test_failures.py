"""02 §6 failure table: consent store down, carrier timeout with/without a Watch, carrier 5xx, policy raises."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError
from fastmcp.exceptions import ToolError
from tower_audit.reader import query_rows
from tower_consent import LastState
from tower_mcp.seed import seed_watch

from .conftest import MOCK_START, Stack

pytestmark = pytest.mark.integration


def _break_store(stack: Stack, operation: str) -> None:
    def fail(**_: Any) -> None:
        raise EndpointConnectionError(endpoint_url="http://dynamodb.invalid")

    stack.inject(operation, fail)


async def test_consent_store_down_is_service_unavailable(stack: Stack) -> None:
    _break_store(stack, "Query")
    before = await stack.calls()
    for call in (stack.line_is_ok, stack.is_reachable):
        r = await call("user-asish", "mom")
        assert r.reason_codes == ["SERVICE_UNAVAILABLE"]
        assert r.summary == "Something on my side isn't available. Try again in a minute."
        assert r.facts.model_dump(exclude_defaults=True) == {"line": "mom"}
    r = await stack.watch_line("user-asish", "self", None)
    assert r.reason_codes == ["SERVICE_UNAVAILABLE"]
    assert await stack.calls() == before  # never guess consent, never ask the carrier


async def test_carrier_timeout_with_watch_is_stale_with_last_known(stack: Stack) -> None:
    last = LastState(
        sim_change_at=MOCK_START - timedelta(days=30),
        cf_status="none",
        reachable=True,
        at=MOCK_START - timedelta(minutes=30),
    )
    seed_watch(stack.store, stack.seed.asish_line, "user-asish", last)
    await stack.fault("timeout", 2)
    r = await stack.line_is_ok("user-asish")
    assert r.reason_codes == ["STALE_DATA"]
    assert (
        r.summary
        == "I can't reach your carrier right now. The last I heard was at 9:30 today."  # 03 §4, neutral (D5)
    )
    assert r.facts.stale is True and r.facts.source == "watch"
    assert r.facts.call_forwarding == "none" and r.facts.sim_swapped_recently is False
    assert r.facts.as_of == last.at and r.checked_at == last.at
    row = query_rows(stack.store, stack.seed.asish_line)[-1]
    assert (row.source, row.outcome, row.reason_codes) == ("watch", "refused", ("STALE_DATA",))


async def test_carrier_timeout_without_watch_is_carrier_error(stack: Stack) -> None:
    await stack.fault("timeout", 2)
    r = await stack.line_is_ok("user-asish")
    assert r.reason_codes == ["CARRIER_ERROR"]
    assert r.summary == "I can't reach your carrier right now. Try again in a minute."
    assert r.facts.sim_swapped_recently is None and r.facts.call_forwarding is None


async def test_carrier_500_is_carrier_error_even_with_a_watch(stack: Stack) -> None:
    seed_watch(
        stack.store,
        stack.seed.asish_line,
        "user-asish",
        LastState(cf_status="none", at=MOCK_START - timedelta(hours=1)),
    )
    await stack.fault("500", 2)
    r = await stack.line_is_ok("user-asish")
    assert r.reason_codes == ["CARRIER_ERROR"]


async def test_reachability_timeout_with_watch_is_stale(stack: Stack) -> None:
    seed_watch(
        stack.store,
        stack.seed.mom_line,
        "user-asish",
        LastState(reachable=True, at=MOCK_START - timedelta(minutes=45)),
        profile="care",
    )
    await stack.fault("timeout", 1)
    r = await stack.is_reachable("user-asish", "mom")
    assert r.reason_codes == ["STALE_DATA"]
    assert r.facts.stale is True and r.facts.reachable is True


async def test_reachability_timeout_without_watch_is_carrier_error(stack: Stack) -> None:
    await stack.fault("timeout", 1)
    assert (await stack.is_reachable("user-asish", "mom")).reason_codes == ["CARRIER_ERROR"]


async def test_policy_raising_is_a_bare_error_and_no_row(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    mod = importlib.import_module("tower_mcp.tools.line_is_ok")

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("policy bug")

    monkeypatch.setattr(mod, "evaluate_line", boom)
    with pytest.raises(ToolError, match="^internal error$"):
        await stack.line_is_ok("user-asish")
    assert query_rows(stack.store, stack.seed.asish_line) == []
