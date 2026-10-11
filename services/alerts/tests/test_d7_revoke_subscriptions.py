"""D7 (code-vs-docs.md): after the line-holder revokes a `watch` grant, Alerts stops watching for that grantee.

Design rule 4 (README: consent is per line, per tool, *revocable*), 04 §3 (revocation is one tap on the page),
06 §4 ("what makes 'revocable any time' true for the proactive path"). Today `revoke` only sets `revoked_at`:
the grantee's Watch stays `enabled`, `kinds_for_line` (subscriptions.py:48) still counts it, the poll's
watchdog (runner.py:93-98) re-subscribes, and `/internal/watch enable=false` re-subscribes instead of
unsubscribing — so the carrier keeps sending Tower events about a line whose consent was withdrawn.

The revoke here is `tower_consent.revoke`, the primitive the binding page's revoke route calls (grants.py:158).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from alerts.internal_api import WatchRequest, set_watch
from alerts.runner import poll, process_line
from alerts.subscriptions import kinds_for_line
from alerts.testing import ASISH, MOM, T0, FakeCarrier, FakeLine, World, audit_rows, row_summary
from tower_consent import get_watch, revoke, upsert_watch
from tower_consent.models import Watch

pytestmark = pytest.mark.integration


async def _asish_watches_mom(world: World, carrier: FakeCarrier) -> str:
    """Mom's line, Asish's `care` Watch enabled the way Tower does it (`POST /internal/watch`)."""
    world.standard()
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60), last_status_time=T0)
    carrier.lines[ASISH] = FakeLine(sim_change_at=T0 - timedelta(days=30), last_status_time=T0)
    mom = world.lines[MOM]
    await set_watch(
        world.svc, WatchRequest(line_id=mom, enable=True, profile="care", watcher_user_id="user-asish")
    )
    assert carrier.subscriptions, "precondition: enabling the watch subscribed the line"
    return mom


def _revoke(world: World, mom: str, at=T0) -> None:  # type: ignore[no-untyped-def]
    revoke(world.store, mom, "user-asish", "watch", revoked_by="user-mom", now=at)


async def test_d7_revoked_grantees_watch_is_no_longer_enabled(world: World, carrier: FakeCarrier) -> None:
    mom = await _asish_watches_mom(world, carrier)
    _revoke(world, mom)
    w = get_watch(world.store, mom, "user-asish")
    assert w is None or not w.enabled, f"the revoked grantee's Watch is still enabled: {w}"


async def test_d7_revoked_watch_not_counted_by_kinds_for_line(world: World, carrier: FakeCarrier) -> None:
    mom = await _asish_watches_mom(world, carrier)
    assert "reachability-disconnected" in kinds_for_line(world.svc, mom)
    _revoke(world, mom)
    assert kinds_for_line(world.svc, mom) == ()


async def test_d7_remaining_watch_keeps_only_what_it_needs(world: World, carrier: FakeCarrier) -> None:
    """Mom's own `self` Watch on her line survives the revoke and needs only the SIM-swap subscription."""
    mom = await _asish_watches_mom(world, carrier)
    upsert_watch(world.store, Watch(line_id=mom, watcher_user_id="user-mom", profile="self"))
    _revoke(world, mom)
    assert kinds_for_line(world.svc, mom) == ("sim-swap",)
    own = get_watch(world.store, mom, "user-mom")
    assert own is not None and own.enabled  # only the grantee's Watch is touched


async def test_d7_watch_off_after_revoke_unsubscribes(world: World, carrier: FakeCarrier) -> None:
    """The documented remedy's call (`/internal/watch enable=false`) after a revoke must leave no live
    subscription on the line — today it re-subscribes because the revoked Watch is still counted."""
    mom = await _asish_watches_mom(world, carrier)
    _revoke(world, mom)
    carrier.calls.clear()
    out = await set_watch(world.svc, WatchRequest(line_id=mom, enable=False, watcher_user_id="user-asish"))
    assert "subscribe" not in carrier.calls
    assert out["subscribed"] == []
    assert {sid: v for sid, v in carrier.subscriptions.items() if v[1] == mom} == {}


async def test_d7_watchdog_does_not_renew_after_revoke(world: World, carrier: FakeCarrier, clock) -> None:  # type: ignore[no-untyped-def]
    """Past TTL/2 with no event, the poll's watchdog re-subscribes a watched line (06 §8). Not a revoked one."""
    mom = await _asish_watches_mom(world, carrier)
    _revoke(world, mom)
    later = T0 + world.svc.settings.subscription_ttl / 2 + timedelta(hours=1)
    clock.set(later)
    carrier.calls.clear()
    await poll(world.svc, "care", later)
    assert "subscribe" not in carrier.calls, f"re-subscribed after revoke: {carrier.calls}"


async def test_d7_no_sms_to_revoked_watcher_on_event(
    world: World, carrier: FakeCarrier, clock, sender
) -> None:  # type: ignore[no-untyped-def]
    """Guard (06 §4, passes today): an event after the revoke texts nobody about it; audited SUPPRESSED_REVOKED."""
    mom = await _asish_watches_mom(world, carrier)
    _revoke(world, mom)
    clock.set(T0 + timedelta(minutes=5))
    carrier.swap(MOM)
    before = len(audit_rows(world.store, mom))
    await process_line(world.svc, mom, "event", clock.at)
    assert sender.sent == []
    assert row_summary(audit_rows(world.store, mom))[before:] == [
        ("event", "suppressed", ("SUPPRESSED_REVOKED",), None)
    ]
