"""Local runner: one FastAPI app (hooks + internal API + health) and an in-process scheduler that calls the
same `poll` the Lambda runs, on the 10 §3 cadences divided by `ALERTS_CLOCK_SCALE`.

    uv run python -m alerts.local          # or: alerts-local

| Job | Simulated cadence | Real interval at scale 60 |
|---|---|---|
| poll `transplant` | 5 min (`ALERTS_TRANSPLANT_POLL_S`) | 5 s |
| poll `care` | 30 min (`ALERTS_CARE_POLL_S`) | 30 s |
| poll `self` | daily (`ALERTS_DAILY_POLL_S`) | 24 min |
| escalation tick | 1 min (`ALERTS_TICK_S`) | 1 s |

The scale only changes how often the jobs wake up. *What time it is* comes from the clock (`ALERTS_CLOCK`):
with `mock`, every evaluation asks the mock carrier, so a 20-minute window closes when the mock clock has
moved 20 minutes — whether a script advanced it in one jump or the showcase stepped it a minute per second.

One loop wakes every tick interval, reads the clock once and runs each job that is **due**: its real interval
has passed, *or* the clock has moved the job's simulated cadence since it last ran. The second rule matters when a
script jumps the mock clock and then changes the line again within a few real seconds (`make demo`'s transplant
story: +20 min, +15 min, back on the network): the transplant poll and the escalation tick then run on the first
wake after each jump instead of up to one real poll interval later, when the line is reachable again (06 §11.4).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import FastAPI

from alerts import escalation, hooks, internal_api
from alerts.context import AlertsService
from alerts.runner import build_service, poll

log = logging.getLogger("alerts.local")


@dataclass
class Job:
    """One scheduled job: its 10 §3 cadence in simulated seconds, and when it last ran (real and simulated)."""

    name: str
    simulated_s: float
    run: Callable[[datetime], Awaitable[Any]]
    last_real: float = 0.0
    last_sim: datetime | None = None


def due(job: Job, *, real_now: float, sim_now: datetime, scale: float) -> bool:
    """The real interval (cadence / scale) has passed, or the clock moved the cadence forward since the last run."""
    if real_now - job.last_real >= job.simulated_s / scale:
        return True
    return job.last_sim is not None and (sim_now - job.last_sim).total_seconds() >= job.simulated_s


class Scheduler:
    def __init__(
        self,
        svc: AlertsService,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        jobs: Sequence[Job] | None = None,
    ) -> None:
        self.svc = svc
        self._monotonic = monotonic
        s = svc.settings
        self.jobs = list(jobs) if jobs is not None else self.default_jobs(svc)
        started = monotonic()
        for job in self.jobs:
            job.last_real = started
        self.wake_s = min(job.simulated_s for job in self.jobs) / s.clock_scale
        self._task: asyncio.Task[None] | None = None

    @staticmethod
    def default_jobs(svc: AlertsService) -> list[Job]:
        s = svc.settings
        return [
            Job("poll:transplant", s.transplant_poll_s, lambda now: poll(svc, "transplant", now)),
            Job("poll:care", s.care_poll_s, lambda now: poll(svc, "care", now)),
            Job("poll:self", s.daily_poll_s, lambda now: poll(svc, "self", now)),
            Job("tick", s.tick_s, lambda now: escalation.tick(svc, now)),
        ]

    async def step(self) -> list[str]:
        """One wake: read the clock once, run every due job in order. Returns the names of the jobs run."""
        now = await self.svc.clock.now()
        real = self._monotonic()
        ran: list[str] = []
        for job in self.jobs:
            if job.last_sim is None or now < job.last_sim:
                job.last_sim = (
                    now  # the first reading, or the mock clock went back (scenario reload): re-baseline
                )
            if not due(job, real_now=real, sim_now=now, scale=self.svc.settings.clock_scale):
                continue
            job.last_real, job.last_sim = real, now
            ran.append(job.name)
            try:
                result = await job.run(now)
                log.debug("job %s → %s", job.name, result)
            except Exception:  # noqa: BLE001 - a failed poll must not stop the scheduler (06 §8)
                log.exception("job %s failed", job.name)
        return ran

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.wake_s)
            try:
                await self.step()
            except Exception:  # noqa: BLE001 - the clock read failed (mock down): try again next wake (06 §8)
                log.exception("scheduler wake failed")

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="alerts-scheduler")
        scale = self.svc.settings.clock_scale
        log.info(
            "scheduler started (scale %.3g, wake every %.3gs): %s",
            scale,
            self.wake_s,
            ", ".join(
                f"{j.name} every {j.simulated_s / scale:.3g}s or {j.simulated_s:.0f} simulated s"
                for j in self.jobs
            ),
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


def create_app(svc: AlertsService, *, scheduler: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        sched = Scheduler(svc) if scheduler else None
        if sched:
            sched.start()
        try:
            yield
        finally:
            if sched:
                await sched.stop()

    app = FastAPI(title="Ask the Tower — Alerts", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.include_router(hooks.router(svc))
    app.include_router(internal_api.router(svc))

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "mode": svc.settings.mode}

    app.state.alerts = svc
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    svc = build_service()
    # k8s: the same app without the in-process scheduler — CronJobs run `python -m alerts.job` (10 §1, §3)
    scheduler = svc.settings.mode == "local"
    uvicorn.run(create_app(svc, scheduler=scheduler), host="0.0.0.0", port=svc.settings.port)  # noqa: S104


if __name__ == "__main__":
    main()
