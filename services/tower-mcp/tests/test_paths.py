"""Path A against the in-process mock (prompt 08 `test_paths.py`, e2e-wiring §3, testing-and-showcase §2.2)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from mock_carrier.testing import ASISH, MOM
from tower_audit.reader import query_rows
from tower_consent import LastState, revoke
from tower_mcp.seed import seed_watch

from .conftest import MOCK_START, SUPPORT, Stack

pytestmark = pytest.mark.integration


async def test_seeded_line_is_ok(stack: Stack) -> None:
    before = await stack.calls()
    r = await stack.line_is_ok("user-asish")
    assert r.reason_codes == ["OK"]
    assert r.summary == "Your line is as it was."
    assert r.facts.sim_swapped_recently is False
    assert r.facts.call_forwarding == "none"
    assert r.facts.source == "carrier"
    assert r.next_step.kind == "none"
    assert r.checked_at == MOCK_START
    assert await stack.calls() - before == 2  # check + forwarding, in parallel (no date: not swapped)
    rows = query_rows(stack.store, stack.seed.asish_line)
    assert [(x.tool, x.source, x.outcome, x.trigger) for x in rows] == [
        ("line_is_ok", "carrier", "ok", "voice")
    ]
    assert rows[0].actor_user_id == "user-asish"


async def test_fresh_watch_answers_from_stored_state(stack: Stack) -> None:
    state = LastState(
        sim_change_at=MOCK_START - timedelta(days=30),
        cf_status="none",
        reachable=True,
        at=MOCK_START - timedelta(minutes=4),
    )
    seed_watch(stack.store, stack.seed.asish_line, "user-asish", state)
    before = await stack.calls()
    r = await stack.line_is_ok("user-asish")
    assert await stack.calls() == before  # zero carrier calls
    assert r.reason_codes == ["OK"]
    assert r.facts.source == "watch"
    assert r.checked_at == state.at
    rows = query_rows(stack.store, stack.seed.asish_line)
    assert rows[-1].source == "watch"


async def test_watch_older_than_fresh_goes_live(stack: Stack) -> None:
    state = LastState(cf_status="none", at=MOCK_START - timedelta(minutes=11))
    seed_watch(stack.store, stack.seed.asish_line, "user-asish", state)
    before = await stack.calls()
    r = await stack.line_is_ok("user-asish")
    assert await stack.calls() - before == 2
    assert r.facts.source == "carrier"
    assert r.reason_codes == ["OK"]
    assert query_rows(stack.store, stack.seed.asish_line)[-1].source == "carrier"


async def test_sim_swap_is_reported_with_its_time(stack: Stack) -> None:
    await stack.advance(12 * 60)  # moment 1: twelve minutes on the mock clock
    await stack.event(ASISH, "sim_swap")
    before = await stack.calls()
    r = await stack.line_is_ok("user-asish")
    assert await stack.calls() - before == 3  # check + forwarding, then the date for {time}
    assert r.reason_codes == ["SIM_SWAPPED_RECENT"]
    # 14:12Z is 10:12 in Ottawa (EDT) — the summary speaks the swap's own time, "today".
    assert (
        r.summary
        == "Your SIM was moved to another device at 10:12 today. If that wasn't you, call your carrier now."
    )
    assert r.facts.sim_swapped_recently is True
    assert r.facts.swapped_at == MOCK_START + timedelta(minutes=12)
    assert r.next_step.kind == "call_carrier"
    assert r.next_step.carrier_support_number == SUPPORT


async def test_call_forwarding_set(stack: Stack) -> None:
    await stack.event(ASISH, "cf_set")
    r = await stack.line_is_ok("user-asish")
    assert r.reason_codes == ["CALL_FORWARDING_SET"]
    assert r.facts.call_forwarding == "unconditional"
    assert r.summary.startswith("All your calls have been forwarding since 10:00 today.")
    assert r.next_step.kind == "call_carrier"


async def test_grantee_alias_sees_moms_line(stack: Stack) -> None:
    r = await stack.line_is_ok("user-asish", "mom")
    assert r.reason_codes == ["OK"]
    assert r.facts.line == "mom"
    await stack.event(MOM, "sim_swap")
    r = await stack.line_is_ok("user-asish", "Mom")
    assert r.reason_codes == ["SIM_SWAPPED_RECENT"]
    rows = query_rows(stack.store, stack.seed.mom_line)
    assert [x.actor_user_id for x in rows] == ["user-asish", "user-asish"]


async def test_unknown_alias_is_no_consent_without_a_binding_url(stack: Stack) -> None:
    """04 §5 / D18: an alias the caller holds no grant for is NO_CONSENT — no bind link for their own line."""
    before = await stack.calls()
    r = await stack.line_is_ok("user-asish", "bob")
    assert r.reason_codes == ["NO_CONSENT"]
    assert r.summary == "Bob hasn't shared that with you."
    assert r.next_step.kind == "ask_consent" and r.next_step.url is None
    assert r.facts.model_dump(exclude_none=True, exclude_defaults=True) == {"line": "bob"}
    assert await stack.calls() == before


async def test_user_without_a_line_is_not_bound(stack: Stack) -> None:
    r = await stack.line_is_ok("user-new")
    assert r.reason_codes == ["NOT_BOUND"]
    token = r.next_step.url.rsplit("/", 1)[1]
    from tower_consent import get_bind_token

    tok = get_bind_token(stack.store, token, now=MOCK_START)
    assert tok is not None and tok.user_id == "user-new"


async def test_revoked_alias_is_no_consent(stack: Stack) -> None:
    revoke(stack.store, stack.seed.mom_line, "user-asish", "watch", revoked_by="user-mom", now=MOCK_START)
    before = await stack.calls()
    r = await stack.line_is_ok("user-asish", "mom")
    assert r.reason_codes == ["NO_CONSENT"]
    assert r.summary == "Mom hasn't shared that with you."
    assert r.next_step.kind == "ask_consent"
    assert r.facts.sim_swapped_recently is None
    assert await stack.calls() == before  # nothing asked of the carrier
    rows = query_rows(stack.store, stack.seed.mom_line)
    assert rows[-1].outcome == "refused" and rows[-1].reason_codes == ("NO_CONSENT",)


async def test_number_shaped_line_is_not_echoed(stack: Stack) -> None:
    r = await stack.line_is_ok("user-asish", ASISH)
    assert r.reason_codes == ["NO_CONSENT"]  # D18: not the caller's own line, so not NOT_BOUND
    assert r.facts.line == "unknown"
    assert ASISH[1:] not in r.model_dump_json()


async def test_is_reachable(stack: Stack) -> None:
    before = await stack.calls()
    r = await stack.is_reachable("user-asish", "mom")
    assert await stack.calls() - before == 1
    assert r.reason_codes == ["OK"]
    assert r.facts.reachable is True and r.facts.connectivity == "DATA"
    await stack.event(MOM, "unreachable")
    r = await stack.is_reachable("user-asish", "mom")
    assert r.reason_codes == ["UNREACHABLE"]
    assert r.facts.reachable is False
    assert r.summary.startswith("Mom's phone has been off the network since")
    assert "location" not in r.model_dump_json()


async def test_reachability_grant_scopes_tools(stack: Stack) -> None:
    from tower_consent import grant

    grant(
        stack.store,
        stack.seed.asish_line,
        "user-mom",
        "reachability",
        "asish",
        granted_by="user-asish",
        now=MOCK_START,
    )
    assert (await stack.is_reachable("user-mom", "asish")).reason_codes == ["OK"]
    before = await stack.calls()
    assert (await stack.line_is_ok("user-mom", "asish")).reason_codes == ["NO_CONSENT"]
    assert (await stack.watch_line("user-mom", "asish", True)).reason_codes == ["NO_CONSENT"]
    assert await stack.calls() == before


async def test_watch_line_enable_disable_and_status(stack: Stack) -> None:
    before = await stack.calls()
    r = await stack.watch_line("user-asish", "mom", True)
    assert r.reason_codes == ["OK"] and r.facts.watching is True and r.facts.notify_via == "sms"
    assert stack.alerts.calls[-1] == {
        "line_id": stack.seed.mom_line,
        "watcher_user_id": "user-asish",
        "enable": True,
        "profile": "care",
    }
    r = await stack.watch_line("user-asish", "mom", None)
    assert r.facts.watching is True
    assert r.facts.grants == [] and r.facts.recent_checks is None  # a watcher never sees who else checked
    r = await stack.watch_line("user-asish", "mom", False)
    assert r.facts.watching is False and stack.alerts.calls[-1]["enable"] is False
    assert await stack.calls() == before  # watch_line never asks the carrier
    rows = query_rows(stack.store, stack.seed.mom_line)
    assert [x.tool for x in rows] == ["watch_line"] * 3


async def test_watch_line_status_for_the_line_holder(stack: Stack) -> None:
    await stack.line_is_ok("user-asish", "mom")
    await stack.line_is_ok("user-mom")
    r = await stack.watch_line("user-mom", "self", None)
    assert r.facts.watching is False
    assert [g.model_dump() for g in r.facts.grants] == [{"alias": "mom", "grant": "watch"}]
    assert r.facts.recent_checks.by_actor == {"user-asish": 1, "self": 1}
    assert r.facts.recent_checks.outcomes["ok"] == 2
    assert r.facts.recent_checks.last_at == MOCK_START


async def test_alerts_failure_does_not_undo_the_watch(stack: Stack) -> None:
    stack.alerts.fail = True
    r = await stack.watch_line("user-asish", "self", True)
    assert r.reason_codes == ["OK"] and r.facts.watching is True
