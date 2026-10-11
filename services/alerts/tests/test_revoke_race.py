"""06 §4: the grant is re-read immediately before any send; revoked → SUPPRESSED_REVOKED, nothing sent."""

from __future__ import annotations

from datetime import timedelta

import pytest
from alerts.evaluate import evaluate
from alerts.runner import apply, process_line
from alerts.testing import MOM, T0, World, audit_rows, row_summary
from tower_consent import revoke

pytestmark = pytest.mark.integration


async def test_revoke_between_evaluate_and_send(world: World, mom: str, carrier, clock, sender) -> None:
    await process_line(world.svc, mom, "poll", T0)
    clock.set(T0 + timedelta(minutes=1))
    carrier.swap(MOM)
    [d] = await evaluate(world.svc, mom, "event", clock.at)
    assert d.notify
    revoke(world.store, mom, "user-asish", "watch", revoked_by="user-mom", now=clock.at)
    result = await apply(world.svc, d)
    assert result is not None and result.revoked
    assert sender.sent == []
    assert row_summary(audit_rows(world.store, mom)) == [
        ("event", "suppressed", ("SUPPRESSED_REVOKED",), None)
    ]


async def test_event_after_revoke_makes_no_carrier_call(
    world: World, mom: str, carrier, clock, sender
) -> None:
    await process_line(world.svc, mom, "poll", T0)
    revoke(world.store, mom, "user-asish", "watch", revoked_by="user-mom", now=T0)
    carrier.calls.clear()
    clock.set(T0 + timedelta(minutes=1))
    carrier.swap(MOM)
    await process_line(world.svc, mom, "event", clock.at)
    await process_line(world.svc, mom, "poll", clock.at)
    assert carrier.calls == []  # consent before facts: nothing is fetched for a watcher who may not see it
    assert sender.sent == []
    # the carrier's event is audited as suppressed; the poll leaves no row (nothing observed, nothing released)
    assert row_summary(audit_rows(world.store, mom)) == [
        ("event", "suppressed", ("SUPPRESSED_REVOKED",), None)
    ]


async def test_reachability_only_grant_does_not_cover_alerts(world: World, carrier, clock, sender) -> None:
    from alerts.testing import ASISH, FakeLine
    from tower_consent import grant

    world.standard()
    revoke(world.store, world.lines[MOM], "user-asish", "watch", revoked_by="user-mom", now=T0)
    grant(world.store, world.lines[MOM], "user-asish", "reachability", "mom", granted_by="user-mom", now=T0)
    world.watch(MOM, "user-asish", "care")
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60))
    carrier.lines[ASISH] = FakeLine()
    [d] = await evaluate(world.svc, world.lines[MOM], "poll", T0)
    assert d.kind == "refused"
