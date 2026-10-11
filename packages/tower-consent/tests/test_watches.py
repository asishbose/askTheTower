"""Watches: upsert keeps last_state; update_last_state is conditional on `at`; list by profile / by line."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tower_consent import (
    EscalationStep,
    LastState,
    Store,
    Watch,
    get_watch,
    list_watches_by_profile,
    list_watches_for_line,
    update_last_state,
    upsert_watch,
)
from tower_consent.errors import WatchNotFound

pytestmark = pytest.mark.integration

LINE = "ln_" + "b" * 64


def _state(now: datetime, **kw: object) -> LastState:
    return LastState(at=now, **kw)  # type: ignore[arg-type]


def test_upsert_and_get_round_trip(store: Store, now: datetime) -> None:
    w = Watch(
        line_id=LINE,
        watcher_user_id="asish",
        profile="care",
        subscription_ids=["sub-a"],
        escalation=[EscalationStep(user_id="asish", requires_ack=True), EscalationStep(user_id="neighbour")],
        last_state=_state(now, reachable=True, cf_status="none", last_alert_at={"SIM_SWAPPED_RECENT": now}),
    )
    saved = upsert_watch(store, w)
    assert saved == w
    assert get_watch(store, LINE, "asish") == w
    assert get_watch(store, LINE, "nobody") is None


def test_upsert_never_overwrites_last_state(store: Store, now: datetime) -> None:
    upsert_watch(store, Watch(line_id=LINE, watcher_user_id="asish", profile="care", last_state=_state(now)))
    newer = _state(now + timedelta(hours=1), reachable=False, unreachable_since=now)
    upsert_watch(
        store, Watch(line_id=LINE, watcher_user_id="asish", profile="care", enabled=False, last_state=newer)
    )
    got = get_watch(store, LINE, "asish")
    assert got is not None and not got.enabled
    assert got.last_state == _state(now)


def test_update_last_state_is_conditional_on_at(store: Store, now: datetime) -> None:
    upsert_watch(store, Watch(line_id=LINE, watcher_user_id="asish", profile="transplant"))
    assert update_last_state(store, LINE, "asish", _state(now, reachable=True))
    assert update_last_state(store, LINE, "asish", _state(now + timedelta(microseconds=1), reachable=False))
    assert not update_last_state(store, LINE, "asish", _state(now, reachable=True))  # older: dropped
    assert not update_last_state(
        store, LINE, "asish", _state(now + timedelta(microseconds=1))
    )  # equal: dropped
    got = get_watch(store, LINE, "asish")
    assert got is not None and got.last_state is not None and got.last_state.reachable is False
    with pytest.raises(WatchNotFound):
        update_last_state(store, LINE, "nobody", _state(now))


def test_lists(store: Store, now: datetime) -> None:
    other = "ln_" + "c" * 64
    upsert_watch(store, Watch(line_id=LINE, watcher_user_id="asish", profile="care"))
    upsert_watch(store, Watch(line_id=LINE, watcher_user_id="mom", profile="self"))
    upsert_watch(store, Watch(line_id=other, watcher_user_id="asish", profile="care", enabled=False))
    assert {w.watcher_user_id for w in list_watches_for_line(store, LINE)} == {"asish", "mom"}
    assert [w.line_id for w in list_watches_by_profile(store, "care")] == [LINE]
    assert len(list_watches_by_profile(store, "care", enabled_only=False)) == 2
    assert list_watches_by_profile(store, "transplant") == []


def test_naive_datetimes_rejected() -> None:
    with pytest.raises(ValueError):
        LastState(at=datetime(2026, 10, 6, 14, 30))  # noqa: DTZ001
