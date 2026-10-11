"""D4 (code-vs-docs.md) at the tool: one of the two carrier calls fails → never "Your line is as it was."

02 §6 failure table: carrier timeout → `STALE_DATA` with last-known facts if a Watch exists, else `CARRIER_ERROR`.
Today `line_is_ok` falls back to the stored Watch state only when *both* calls failed (`line_is_ok.py:167-169`)
and the engine refuses only when both facts are unknown (`engine.py:43`), so a SIM swap check that times out next
to a call-forwarding "none" is spoken as OK.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from camara_client import CarrierError
from tower_audit.reader import query_rows
from tower_consent import LastState
from tower_mcp.seed import seed_watch
from tower_policy import ReasonCode

from .conftest import MOCK_START, Stack

pytestmark = pytest.mark.integration

OK_SENTENCE = "Your line is as it was."


def _fail(stack: Stack, monkeypatch: pytest.MonkeyPatch, call: str, reason: ReasonCode, kind: str) -> None:
    """Make exactly one of the carrier client's calls fail (the other still answers from the mock)."""

    async def boom(*_: Any, **__: Any) -> Any:
        raise CarrierError(reason, True, kind=kind)

    monkeypatch.setattr(stack.deps.carrier, call, boom)


def _swap_timeout(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    _fail(stack, monkeypatch, "sim_swap_check", ReasonCode.STALE_DATA, "timeout")


async def test_d4_swap_check_timeout_without_watch_is_carrier_error(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    _swap_timeout(stack, monkeypatch)
    r = await stack.line_is_ok("user-asish")
    assert r.summary != OK_SENTENCE
    assert r.reason_codes == ["CARRIER_ERROR"]
    assert r.facts.sim_swapped_recently is None
    row = query_rows(stack.store, stack.seed.asish_line)[-1]
    assert (row.outcome, row.reason_codes) == ("refused", ("CARRIER_ERROR",))


async def test_d4_swap_check_timeout_with_watch_is_stale_from_watch(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Watch older than FRESH exists → the timeout falls back to its last-known facts, marked stale."""
    last = LastState(
        sim_change_at=MOCK_START - timedelta(days=30),
        cf_status="none",
        reachable=True,
        at=MOCK_START - timedelta(minutes=30),
    )
    seed_watch(stack.store, stack.seed.asish_line, "user-asish", last)
    _swap_timeout(stack, monkeypatch)
    r = await stack.line_is_ok("user-asish")
    assert r.summary != OK_SENTENCE
    assert r.reason_codes == ["STALE_DATA"]
    assert r.facts.source == "watch" and r.facts.stale is True
    assert r.facts.as_of == last.at and r.checked_at == last.at
    row = query_rows(stack.store, stack.seed.asish_line)[-1]
    assert (row.source, row.outcome, row.reason_codes) == ("watch", "refused", ("STALE_DATA",))


async def test_d4_swap_check_timeout_never_hides_a_stored_swap(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Watch last saw a swap inside the window; the live swap check times out, forwarding says none.
    The answer must not be OK — the last-known facts (with the swap) are what 02 §6 says to use."""
    last = LastState(
        sim_change_at=MOCK_START - timedelta(hours=2),
        cf_status="none",
        at=MOCK_START - timedelta(minutes=30),
    )
    seed_watch(stack.store, stack.seed.asish_line, "user-asish", last)
    _swap_timeout(stack, monkeypatch)
    r = await stack.line_is_ok("user-asish")
    assert r.summary != OK_SENTENCE
    assert r.reason_codes == ["STALE_DATA"]
    assert r.facts.source == "watch" and r.facts.sim_swapped_recently is True


async def test_d4_swap_check_500_is_carrier_error_even_with_a_watch(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not a timeout (a 5xx): no stale fallback (as test_failures' both-calls 500 case), and not OK."""
    seed_watch(
        stack.store,
        stack.seed.asish_line,
        "user-asish",
        LastState(cf_status="none", at=MOCK_START - timedelta(hours=1)),
    )
    _fail(stack, monkeypatch, "sim_swap_check", ReasonCode.CARRIER_ERROR, "http")
    r = await stack.line_is_ok("user-asish")
    assert r.summary != OK_SENTENCE
    assert r.reason_codes == ["CARRIER_ERROR"]


async def test_d4_forwarding_timeout_without_watch_is_not_ok(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirror case: the swap check answers "no", call forwarding times out."""
    _fail(stack, monkeypatch, "call_forwarding", ReasonCode.STALE_DATA, "timeout")
    r = await stack.line_is_ok("user-asish")
    assert r.summary != OK_SENTENCE
    assert r.reason_codes == ["CARRIER_ERROR"]
