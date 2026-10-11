"""06 §1/§9: an event and a poll producing the same facts produce the same decision and at most one alert."""

from __future__ import annotations

from datetime import timedelta

import pytest
from alerts.evaluate import evaluate
from alerts.runner import apply, process_line
from alerts.testing import MOM, T0, World, audit_rows, row_summary
from tower_audit import SYSTEM_ALERTS
from tower_audit.record import encode_digest
from tower_policy import ReasonCode, policy_version

pytestmark = pytest.mark.integration


async def test_event_and_poll_agree_and_alert_once(world: World, mom: str, carrier, clock, sender) -> None:
    await process_line(world.svc, mom, "poll", T0)  # baseline: a swap 60 days ago is not news
    assert sender.sent == []
    clock.set(T0 + timedelta(minutes=1))
    carrier.swap(MOM)

    [by_event] = await evaluate(world.svc, mom, "event", clock.at)
    [by_poll] = await evaluate(world.svc, mom, "poll", clock.at)
    assert by_event.comparable() == by_poll.comparable()
    assert by_event.notify and by_event.codes == (ReasonCode.SIM_SWAPPED_RECENT,)

    # both race to release: exactly one SMS; the loser is audited, not sent
    await apply(world.svc, by_event)
    await apply(world.svc, by_poll)
    assert [s.label for s in sender.sent] == ["chain:user-asish"]
    rows = audit_rows(world.store, mom)
    assert row_summary(rows) == [
        ("event", "changed", ("SIM_SWAPPED_RECENT",), "SIM_SWAPPED_RECENT.sms"),
        ("poll", "suppressed", ("SIM_SWAPPED_RECENT",), None),
    ]
    assert {r.actor_user_id for r in rows} == {SYSTEM_ALERTS}
    assert {r.policy_version for r in rows} == {encode_digest(policy_version())}  # same hash as Tower's rows


async def test_poll_after_event_sees_no_change(world: World, mom: str, carrier, clock, sender) -> None:
    await process_line(world.svc, mom, "poll", T0)
    clock.set(T0 + timedelta(minutes=1))
    carrier.swap(MOM)
    await process_line(world.svc, mom, "event", clock.at)
    clock.set(T0 + timedelta(minutes=6))
    [d] = await evaluate(world.svc, mom, "poll", clock.at)
    assert not d.notify and d.kind == "evaluated"
    await apply(world.svc, d)
    assert len(sender.sent) == 1
    assert len(audit_rows(world.store, mom)) == 1


async def test_poll_first_then_event_is_symmetric(world: World, mom: str, carrier, clock, sender) -> None:
    await process_line(world.svc, mom, "poll", T0)
    clock.set(T0 + timedelta(minutes=1))
    carrier.swap(MOM)
    await process_line(world.svc, mom, "poll", clock.at)
    await process_line(world.svc, mom, "event", clock.at + timedelta(seconds=5))
    assert len(sender.sent) == 1
    assert row_summary(audit_rows(world.store, mom)) == [
        ("poll", "changed", ("SIM_SWAPPED_RECENT",), "SIM_SWAPPED_RECENT.sms")
    ]


async def test_call_forwarding_change_and_baseline(world: World, mom: str, carrier, clock, sender) -> None:
    carrier.lines[MOM].call_forwarding = "unconditional"
    await process_line(world.svc, mom, "poll", T0)  # baseline: already forwarding when the watch started
    assert sender.sent == []
    carrier.lines[MOM].call_forwarding = "none"
    clock.set(T0 + timedelta(minutes=5))
    await process_line(world.svc, mom, "poll", clock.at)
    carrier.lines[MOM].call_forwarding = "unconditional"
    clock.set(T0 + timedelta(minutes=10))
    await process_line(world.svc, mom, "poll", clock.at)
    # not a swap: the line-holder is told at her own number, and the watcher
    assert [s.label for s in sender.sent] == ["line_holder:user-mom", "chain:user-asish"]
    assert sender.sent[0].body.startswith("All calls forwarding since")
