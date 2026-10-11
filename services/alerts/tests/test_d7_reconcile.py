"""D7 (code-vs-docs.md): the watchdog reconciles a line's carrier subscriptions against its enabled, consented
Watches (06 §4, §8). Covers what the tester's file does not: the poll actually *removes* the subscriptions
after a revoke, a remaining Watch narrows them, and a Watch whose grant was revoked but which is still marked
enabled (the revoke's second write missed) is ignored all the same.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from alerts import state as S
from alerts.internal_api import WatchRequest, set_watch
from alerts.runner import poll
from alerts.subscriptions import kinds_for_line, watchdog
from alerts.testing import ASISH, MOM, T0, FakeCarrier, FakeLine, World
from tower_consent import revoke, upsert_watch
from tower_consent import tables as T
from tower_consent.models import Watch

pytestmark = pytest.mark.integration


async def _asish_watches_mom(world: World, carrier: FakeCarrier) -> str:
    world.standard()
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60), last_status_time=T0)
    carrier.lines[ASISH] = FakeLine(sim_change_at=T0 - timedelta(days=30), last_status_time=T0)
    mom = world.lines[MOM]
    await set_watch(
        world.svc, WatchRequest(line_id=mom, enable=True, profile="care", watcher_user_id="user-asish")
    )
    return mom


def _live_kinds(carrier: FakeCarrier, line_id: str) -> set[str]:
    return {kind for kind, line, _ in carrier.subscriptions.values() if line == line_id}


async def test_poll_after_revoke_deletes_the_lines_subscriptions(world: World, carrier: FakeCarrier) -> None:
    mom = await _asish_watches_mom(world, carrier)
    assert _live_kinds(carrier, mom)
    revoke(world.store, mom, "user-asish", "watch", revoked_by="user-mom", now=T0)
    await poll(world.svc, "care", T0 + timedelta(minutes=1))
    assert _live_kinds(carrier, mom) == set()
    row = S.get_line_row(world.store, mom)
    assert row is None or row.subscription_ids == []
    carrier.calls.clear()
    await poll(world.svc, "care", T0 + world.svc.settings.subscription_ttl)  # and never comes back
    assert "subscribe" not in carrier.calls


async def test_watchdog_narrows_to_what_the_remaining_watch_needs(world: World, carrier: FakeCarrier) -> None:
    mom = await _asish_watches_mom(world, carrier)
    upsert_watch(world.store, Watch(line_id=mom, watcher_user_id="user-mom", profile="self"))
    revoke(world.store, mom, "user-asish", "watch", revoked_by="user-mom", now=T0)
    # well inside TTL/2, so only the change in what is needed makes it re-subscribe
    assert await watchdog(world.svc, mom, T0 + timedelta(minutes=1))
    assert _live_kinds(carrier, mom) == {"sim-swap"}
    assert not await watchdog(world.svc, mom, T0 + timedelta(minutes=2))  # settled


async def test_enabled_watch_with_a_revoked_grant_is_ignored(world: World, carrier: FakeCarrier) -> None:
    """Defence in depth: the grant is revoked but the Watch row still says enabled."""
    mom = await _asish_watches_mom(world, carrier)
    revoke(world.store, mom, "user-asish", "watch", revoked_by="user-mom", now=T0)
    world.store.update(  # simulate the missed second write of `revoke`
        T.WATCHES,
        {"line_id": mom, "watcher_user_id": "user-asish"},
        "SET enabled = :t",
        values={":t": True},
    )
    assert kinds_for_line(world.svc, mom) == ()
    carrier.calls.clear()
    await poll(world.svc, "care", T0 + world.svc.settings.subscription_ttl)
    assert "subscribe" not in carrier.calls
    assert _live_kinds(carrier, mom) == set()
