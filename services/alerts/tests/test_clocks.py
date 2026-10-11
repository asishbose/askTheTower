"""06 §7 "Clocks" (human decision 3 of 2026-10-11): two clocks, two jobs.

- Every DynamoDB TTL Alerts writes (`expires_at` on `AlertsState` and `SentSms`) is the **wall** time of the write
  plus the row's horizon, whatever the service clock (the mock's scenario clock locally) says. DynamoDB, and
  DynamoDB Local 2.x, delete by the wall clock: a TTL from a scenario clock days behind deleted rows within a
  second (build-log/22-ops.md R1).
- Every window (rate limit, `ESCALATE_NEXT`) compares against the service clock and still holds after the row's
  TTL has passed in wall time: TTL is garbage collection, never a decision.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from alerts import escalation
from alerts import state as S
from alerts.runner import process_line
from alerts.testing import ASISH, T0, TEST_WALL, FakeLine, World
from tower_consent import Store
from tower_policy import Facts, ReasonCode

pytestmark = pytest.mark.integration

M = timedelta(minutes=1)
H = timedelta(hours=1)
RATE = timedelta(hours=6)
WALL = datetime(2026, 10, 11, 9, 30, tzinfo=UTC)
SIMS = (T0, T0 - timedelta(days=30), T0 + timedelta(days=400))  # behind, far behind, far ahead of WALL


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def _row(store: Store, pk: str) -> dict[str, object]:
    item = store.get(S.ALERTS_STATE, {"pk": pk})
    assert item is not None, pk
    return item


def _esc(sent_at: datetime) -> S.Escalation:
    return S.Escalation(
        line_id="ln_" + "a" * 64,
        watcher_user_id="user-partner",
        codes=[ReasonCode.UNREACHABLE],
        facts=Facts(fetched_at=sent_at),
        tz="America/Toronto",
        step=0,
        sent_at=sent_at,
    )


@pytest.mark.parametrize("sim", SIMS, ids=("sim-behind", "sim-far-behind", "sim-ahead"))
def test_every_alerts_state_ttl_is_wall_plus_horizon(store: Store, sim: datetime) -> None:
    line = "ln_" + "a" * 64
    assert S.claim_event(store, "tok", "src", f"evt-{sim.isoformat()}", WALL)
    assert S.claim_alert(store, line, "user-partner", "UNREACHABLE", sim, RATE, wall=WALL)
    S.save_escalation(store, _esc(sim), wall=WALL)
    S.remember_ack_route(store, "ln_" + "b" * 64, line, "user-partner", WALL)

    evt = next(i for i in store.scan_all(S.ALERTS_STATE) if str(i["pk"]).startswith("evt#"))
    assert evt["expires_at"] == _epoch(WALL + S.EVENT_TTL)
    assert _row(store, f"rl#{line}#user-partner#UNREACHABLE")["expires_at"] == _epoch(WALL + RATE)
    assert _row(store, f"esc#{line}#user-partner")["expires_at"] == _epoch(WALL + S.ESC_TTL)
    assert _row(store, f"ack#ln_{'b' * 64}")["expires_at"] == _epoch(WALL + S.ESC_TTL)
    # the window's own timestamps stay on the service clock
    esc = S.get_escalation(store, line, "user-partner")
    assert esc is not None and esc.sent_at == sim


def test_rate_limit_window_runs_on_the_service_clock_after_its_ttl_passed(store: Store) -> None:
    line = "ln_" + "c" * 64

    def take(now: datetime, wall: datetime) -> bool:
        return S.claim_alert(store, line, "w", "UNREACHABLE", now, RATE, wall=wall)

    assert take(T0, WALL)
    # the row's TTL (WALL + 6 h) passed in wall time, but only 1 h passed on the service clock: still limited
    assert not take(T0 + H, WALL + 7 * H)
    assert not take(T0 + 6 * H - M, WALL + 7 * H)
    # 6 h on the service clock, while the wall clock has not even reached the TTL: free again
    assert take(T0 + 6 * H, WALL + M)


async def test_escalation_window_runs_on_the_service_clock_after_its_ttl_passed(
    world: World, carrier, clock, sender
) -> None:
    world.standard()
    world.grant_watch(ASISH, "user-asish", "user-partner", "asish")
    world.watch(ASISH, "user-partner", "transplant", [("user-partner", True), ("user-neighbour", False)])
    carrier.lines[ASISH] = FakeLine(sim_change_at=T0 - timedelta(days=30), last_status_time=T0)
    line = world.lines[ASISH]
    await process_line(world.svc, line, "poll", T0)
    clock.set(T0 + M)
    carrier.set_reachable(ASISH, False)
    await process_line(world.svc, line, "event", clock.at)
    clock.set(T0 + 21 * M)
    await process_line(world.svc, line, "poll", clock.at)
    assert [s.label for s in sender.sent] == ["line_holder:user-asish", "chain:user-partner"]
    parked = _row(world.store, f"esc#{line}#user-partner")
    assert parked["expires_at"] == _epoch(TEST_WALL + S.ESC_TTL)  # make_service's wall, not T0

    world.svc.wall = lambda: TEST_WALL + 3 * S.ESC_TTL  # the row's TTL is long past in wall time
    clock.set(T0 + 35 * M)  # 14 min after the partner's text on the service clock
    assert await escalation.tick(world.svc, clock.at) == []
    clock.set(T0 + 36 * M)  # 15 min: ESCALATE_NEXT
    assert await escalation.tick(world.svc, clock.at) == ["chain:user-neighbour"]


async def test_every_ttl_a_delivery_writes_is_wall_based(world: World, carrier, clock, sender) -> None:
    """The whole path (webhook-free event, rate-limit claim, park, ack route) through `svc.wall`."""
    world.standard()
    world.grant_watch(ASISH, "user-asish", "user-partner", "asish")
    world.watch(ASISH, "user-partner", "transplant", [("user-partner", True), ("user-neighbour", False)])
    carrier.lines[ASISH] = FakeLine(sim_change_at=T0 - timedelta(days=30), last_status_time=T0)
    line = world.lines[ASISH]
    await process_line(world.svc, line, "poll", T0)
    clock.set(T0 + M)
    carrier.set_reachable(ASISH, False)
    await process_line(world.svc, line, "event", clock.at)
    clock.set(T0 + 21 * M)
    await process_line(world.svc, line, "poll", clock.at)

    rows = world.store.scan_all(S.ALERTS_STATE)
    ttls = {str(i["pk"]).split("#")[0]: int(i["expires_at"]) for i in rows if "expires_at" in i}
    assert set(ttls) == {"rl", "esc", "ack"}
    lo, hi = _epoch(TEST_WALL), _epoch(TEST_WALL + S.EVENT_TTL)
    assert all(lo < v <= hi for v in ttls.values()), ttls
