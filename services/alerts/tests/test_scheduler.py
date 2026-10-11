"""The local scheduler (`alerts.local.Scheduler`, 06 §11.4, README "ALERTS_CLOCK_SCALE and the mock clock").

A job is due when its real interval (the 10 §3 cadence divided by `ALERTS_CLOCK_SCALE`) has passed **or** the
clock it evaluates on has moved its simulated cadence since it last ran. The second rule is what lets a story
that jumps the mock clock 20 minutes, then 15 more, then brings the line back within a few real seconds still get
the partner's and the neighbour's texts (build-log/21.md, "transplant texts"): before it, the transplant poll woke
only every 5 real seconds and the line was reachable again by then.

Real time is a fake `monotonic` that never moves, so every run below is caused by the simulated clock alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from alerts.clock import FixedClock
from alerts.config import Settings
from alerts.local import Job, Scheduler, due
from alerts.runner import process_line
from alerts.testing import ASISH, T0, FakeLine, World

M = timedelta(minutes=1)
OWNER, P, N = "user-asish", "user-partner", "user-neighbour"


def frozen() -> float:
    return 100.0


def stub(clock: FixedClock) -> Any:
    """What the scheduler reads from the service: settings (the scale) and the clock."""
    return SimpleNamespace(settings=Settings(clock_scale=60.0), clock=clock)


# --- due(): pure -----------------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("real_elapsed", "sim_elapsed", "expected"),
    [
        (0.0, timedelta(0), False),  # nothing moved
        (4.9, timedelta(minutes=4), False),  # neither cadence reached
        (5.0, timedelta(0), True),  # the real interval (300 s / scale 60)
        (0.0, timedelta(minutes=5), True),  # the simulated cadence: a mock-clock jump
        (0.0, timedelta(minutes=20), True),
        (0.0, -timedelta(minutes=30), False),  # the mock clock went back (a scenario reload)
        (0.0, None, False),  # no simulated reading yet
    ],
)
def test_due_on_real_or_simulated_cadence(
    real_elapsed: float, sim_elapsed: timedelta | None, expected: bool
) -> None:
    job = Job("poll:transplant", 300.0, _never, last_real=10.0, last_sim=None if sim_elapsed is None else T0)
    sim_now = T0 if sim_elapsed is None else T0 + sim_elapsed
    assert due(job, real_now=10.0 + real_elapsed, sim_now=sim_now, scale=60.0) is expected


async def _never(now: datetime) -> Any:
    raise AssertionError("not expected to run")


# --- step(): which jobs a wake runs ---------------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime]] = []

    def job(self, name: str, simulated_s: float) -> Job:
        async def run(now: datetime) -> None:
            self.calls.append((name, now))

        return Job(name, simulated_s, run)


@pytest.mark.unit
async def test_a_wake_runs_each_job_whose_simulated_cadence_passed() -> None:
    clock = FixedClock(T0)
    rec = Recorder()
    jobs = [rec.job("poll:transplant", 300), rec.job("poll:care", 1800), rec.job("tick", 60)]
    sched = Scheduler(stub(clock), monotonic=frozen, jobs=jobs)

    assert await sched.step() == []  # the first wake only takes the simulated baseline
    clock.set(T0 + 20 * M)
    assert await sched.step() == ["poll:transplant", "tick"]  # 20 min ≥ 5 and ≥ 1, < 30
    assert [now for _, now in rec.calls] == [T0 + 20 * M] * 2  # one clock read per wake, passed in
    assert await sched.step() == []  # nothing moved since
    clock.set(T0 + 50 * M)
    assert await sched.step() == ["poll:transplant", "poll:care", "tick"]


@pytest.mark.unit
async def test_a_clock_that_goes_back_is_a_new_baseline() -> None:
    """A scenario reload moves the mock clock back; the cadence counts from there, not from the old reading."""
    clock = FixedClock(T0 + 60 * M)
    rec = Recorder()
    sched = Scheduler(stub(clock), monotonic=frozen, jobs=[rec.job("poll:transplant", 300)])
    await sched.step()
    clock.set(T0)
    assert await sched.step() == []
    clock.set(T0 + 5 * M)
    assert await sched.step() == ["poll:transplant"]


@pytest.mark.unit
async def test_a_failing_job_does_not_stop_the_others() -> None:
    clock = FixedClock(T0)
    rec = Recorder()

    async def boom(now: datetime) -> None:
        raise RuntimeError("carrier down")

    sched = Scheduler(
        stub(clock), monotonic=frozen, jobs=[Job("poll:transplant", 300, boom), rec.job("tick", 60)]
    )
    await sched.step()
    clock.set(T0 + 10 * M)
    assert await sched.step() == ["poll:transplant", "tick"]
    assert [name for name, _ in rec.calls] == ["tick"]


# --- the compressed transplant story over the real jobs -------------------------------------------------


@pytest.mark.integration
async def test_compressed_transplant_story_texts_partner_then_neighbour(
    world: World, carrier: Any, clock: Any, sender: Any
) -> None:
    """The story's shape (ref_client.demo "transplant"): dark, +20 in one jump, +15 in one jump, reachable —
    with no real time passing. One wake after each jump is enough for each text (06 §11.4)."""
    world.standard()
    world.grant_watch(ASISH, OWNER, P, "asish")
    world.grant_watch(ASISH, OWNER, N, "asish")
    world.watch(ASISH, OWNER, "transplant", [(P, True), (N, False)])
    carrier.lines[ASISH] = FakeLine(sim_change_at=T0 - timedelta(days=30), last_status_time=T0)
    line = world.lines[ASISH]
    await process_line(world.svc, line, "poll", T0)  # watch_line's baseline
    carrier.set_reachable(ASISH, False)
    await process_line(world.svc, line, "event", T0)  # the disconnect webhook

    sched = Scheduler(world.svc, monotonic=frozen)
    await sched.step()
    chain = lambda: [s.label for s in sender.sent if s.label.startswith("chain:")]  # noqa: E731 - one-line view

    clock.set(T0 + 20 * M)
    await sched.step()
    assert chain() == [f"chain:{P}"]

    clock.set(T0 + 35 * M)
    await sched.step()
    assert chain() == [f"chain:{P}", f"chain:{N}"]
