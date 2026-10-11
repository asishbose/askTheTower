"""06 §2 window arithmetic: transplant 20 min continuous-false, reset on any true; care 4 h, daytime only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from alerts.runner import process_line
from alerts.testing import ASISH, T0, FakeLine, World
from alerts.windows import is_daytime, next_unreachable_since, rate_limited, unreachable_due
from tower_consent import LastState
from tower_policy import ReasonCode, default_thresholds

TH = default_thresholds()
TZ = "America/Toronto"
M = timedelta(minutes=1)


def observe(seq: list[tuple[int, bool]], profile: str = "transplant", start: datetime = T0) -> list[int]:
    """Feed (minute, reachable) observations through the pure functions; return the minutes that alert."""
    prev: LastState | None = None
    alerts: list[int] = []
    for minute, reachable in seq:
        now = start + minute * M
        since = next_unreachable_since(prev, reachable, None, now)
        last = prev.last_alert_at.get("UNREACHABLE") if prev else None
        stamps = dict(prev.last_alert_at) if prev else {}
        if not reachable and unreachable_due(profile, since, now, TH, TZ, last):
            alerts.append(minute)
            stamps["UNREACHABLE"] = now
        prev = LastState(reachable=reachable, unreachable_since=since, last_alert_at=stamps, at=now)
    return alerts


@pytest.mark.unit
def test_transplant_19_minutes_dark_is_nothing() -> None:
    assert observe([(0, False), (5, False), (10, False), (19, False)]) == []


@pytest.mark.unit
def test_transplant_20_minutes_dark_alerts_once() -> None:
    assert observe([(0, False), (5, False), (19, False), (20, False), (25, False), (40, False)]) == [20]


@pytest.mark.unit
def test_true_at_minute_10_resets_the_clock() -> None:
    seq = [(0, False), (5, False), (10, True), (11, False), (20, False), (30, False), (31, False)]
    assert observe(seq) == [31]


@pytest.mark.unit
def test_failed_observation_is_not_an_observation() -> None:
    # a miss keeps last_state (runner); the pure clock only ever sees real observations
    assert observe([(0, False), (20, False)]) == [20]


@pytest.mark.unit
def test_carrier_status_time_starts_the_clock_but_never_before_our_last_true() -> None:
    prev = LastState(reachable=True, at=T0 + 5 * M)
    assert next_unreachable_since(prev, False, T0 + 2 * M, T0 + 8 * M) == T0 + 5 * M
    assert next_unreachable_since(prev, False, T0 + 6 * M, T0 + 8 * M) == T0 + 6 * M
    assert next_unreachable_since(None, False, None, T0) == T0
    assert next_unreachable_since(prev, True, None, T0 + 8 * M) is None


@pytest.mark.unit
def test_care_four_hours_inside_08_to_22_only() -> None:
    # 14:00Z = 10:00 Toronto. Dark from 10:00 local: 3h59 → nothing; 4h → alert (14:00 local).
    assert observe([(0, False), (239, False), (240, False)], profile="care") == [240]
    # Dark from 19:00 local (23:00Z): 4 h later is 23:00 local — quiet; first evaluation after 08:00 alerts.
    evening = datetime(2026, 10, 5, 23, 0, tzinfo=UTC)
    mins = [(0, False), (240, False), (300, False), (600, False), (780, False), (781, False)]
    # 780 min after 19:00 local = 08:00 local next day
    assert observe(mins, profile="care", start=evening) == [780]


@pytest.mark.unit
def test_daytime_boundaries() -> None:
    assert not is_daytime(datetime(2026, 10, 5, 11, 59, tzinfo=UTC), TZ)  # 07:59 local
    assert is_daytime(datetime(2026, 10, 5, 12, 0, tzinfo=UTC), TZ)  # 08:00
    assert is_daytime(datetime(2026, 10, 6, 1, 59, tzinfo=UTC), TZ)  # 21:59
    assert not is_daytime(datetime(2026, 10, 6, 2, 0, tzinfo=UTC), TZ)  # 22:00


@pytest.mark.unit
def test_self_profile_has_no_reachability_alert() -> None:
    assert observe([(0, False), (600, False)], profile="self") == []


@pytest.mark.unit
def test_rate_limit_window() -> None:
    st = LastState(last_alert_at={"SIM_SWAPPED_RECENT": T0}, at=T0)
    assert rate_limited(st, ReasonCode.SIM_SWAPPED_RECENT, T0 + timedelta(hours=5, minutes=59), TH)
    assert not rate_limited(st, ReasonCode.SIM_SWAPPED_RECENT, T0 + timedelta(hours=6), TH)
    assert not rate_limited(st, ReasonCode.CALL_FORWARDING_SET, T0, TH)


@pytest.mark.integration
async def test_transplant_window_through_the_service(world: World, carrier, clock, sender) -> None:
    """The same arithmetic end to end: polls every 5 min, dark at +1 → first text at the +21 poll (20 min)."""
    world.standard()
    world.grant_watch(ASISH, "user-asish", "user-partner", "asish")
    world.watch(ASISH, "user-partner", "transplant", [("user-partner", True), ("user-neighbour", False)])
    carrier.lines[ASISH] = carrier_line = FakeLine(sim_change_at=T0 - timedelta(days=30), last_status_time=T0)
    line_id = world.lines[ASISH]
    await process_line(world.svc, line_id, "poll", T0)  # baseline
    clock.set(T0 + M)
    carrier.set_reachable(ASISH, False)
    assert carrier_line.reachable is False
    texted: dict[int, list[str]] = {}
    for minute in (1, 6, 11, 16, 20, 21, 26):
        clock.set(T0 + minute * M)
        before = len(sender.sent)
        await process_line(world.svc, line_id, "poll", clock.at)
        texted[minute] = [s.label for s in sender.sent[before:]]
    assert all(not v for m, v in texted.items() if m < 21), texted
    assert texted[21] == ["line_holder:user-asish", "chain:user-partner"]  # 06 §3: line-holder told too
    assert texted[26] == []
    assert "off the network since 10:01 today" in sender.sent[-1].body
