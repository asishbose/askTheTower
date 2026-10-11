"""Macros (doc 11 §5): the four buttons run `ref_client.demo.STORIES` through `run_demo(reset=False, on_step=…)`,
the same code and steps as `make demo`. One timeline: a button runs Reset (the `make seed` code), replays the
earlier stories (marked `replay`), then its own story. One run at a time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

import httpx
from ref_client.agent import Agent
from ref_client.demo import STORY_NAMES, Control, DemoError, StepReport, run_demo
from ref_client.mcp_client import TowerError

from demo_ui import views
from demo_ui.clients import Unavailable

log = logging.getLogger("demo_ui.runner")

MACROS: tuple[tuple[str, str], ...] = (
    ("moment-1", "Moment 1"),
    ("moment-2", "Moment 2"),
    ("moment-3", "Moment 3"),
    ("transplant", "Transplant"),
)


class Busy(RuntimeError):
    """A run is already going."""


def plan(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(stories to run, the ones only replayed) for the macro `name`: everything up to it on one timeline."""
    if name not in STORY_NAMES:
        raise KeyError(name)
    idx = STORY_NAMES.index(name)
    return STORY_NAMES[: idx + 1], STORY_NAMES[:idx]


AgentSession = Callable[[], AbstractAsyncContextManager[Callable[[str], Awaitable[Agent]]]]
"""Opens Tower sessions for one run: yields `agent_for(user_id)`; closes them when the run ends."""


@dataclass
class RunResult:
    name: str
    steps: int = 0
    failed: int = 0
    error: str | None = None


class Runner:
    def __init__(
        self,
        *,
        reset: Callable[[], Awaitable[None]],
        control: Callable[[], Control],
        agents: AgentSession,
        publish: Callable[[str, str], None],
        agent_label: str,
        local: bool = True,
    ) -> None:
        self.reset = reset
        self.control = control
        self.agents = agents
        self.publish = publish
        self.agent_label = agent_label
        self.local = local
        self.task: asyncio.Task[RunResult] | None = None

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self, name: str) -> asyncio.Task[RunResult]:
        """Start the macro in the background; `Busy` if one is running, `KeyError` for an unknown name."""
        plan(name)
        if self.busy:
            raise Busy("a macro is already running")
        self.task = asyncio.create_task(self.run(name), name=f"macro:{name}")
        return self.task

    def _status(self, text: str, level: str = "info") -> None:
        self.publish("status", views.render("status.html", text=text, level=level))

    async def run(self, name: str) -> RunResult:
        stories, replay = plan(name)
        result = RunResult(name)
        log.info("macro start name=%s stories=%d", name, len(stories))

        def on_step(r: StepReport) -> None:
            result.steps += 1
            result.failed += r.ok is False
            self.publish("turn", views.step_fragment(r, agent=self.agent_label))
            url = ((r.turn.result or {}).get("next_step") or {}).get("url") if r.turn else None
            if url:  # NOT_BOUND: the bind link as a QR code in pane 4 (D1: nothing texts it)
                self.publish(
                    "qr", views.bind_fragment(views.with_phone(str(url), "asish", self.local), "asish")
                )

        try:
            self._status(f"{name}: Reset (make seed)…")
            await self.reset()
            self._status(f"{name}: running {', '.join(stories)}")
            async with self.agents() as agent_for:
                await run_demo(
                    agent_for,
                    self.control(),
                    stories=stories,
                    reset=False,
                    on_step=on_step,
                    replay=replay,
                    echo=lambda _line: None,
                )
        except (TowerError, DemoError, Unavailable, httpx.HTTPError) as e:
            result.error = f"{type(e).__name__}: {e}"
            self._status(f"{name} stopped: {result.error}", "fail")
            log.warning("macro stopped name=%s error=%s", name, type(e).__name__)
            return result
        level = "pass" if result.failed == 0 else "fail"
        self._status(f"{name}: {result.steps} steps, {result.failed} mismatched", level)
        log.info("macro done name=%s steps=%d failed=%d", name, result.steps, result.failed)
        return result
