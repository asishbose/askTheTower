"""Doc 11 §5: macros = `STORIES` through `run_demo(reset=False, on_step=…)`: Reset first, earlier stories
replayed, one run at a time, mismatches shown, failures stop the run with the reason. Fakes only."""

from __future__ import annotations

import asyncio
import itertools
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from demo_ui.runner import MACROS, Busy, Runner, RunResult, plan
from ref_client import demo as demo_mod
from ref_client.demo import STORIES, Control
from ref_client.mcp_client import TowerError

from .fakes import MOM_REF, STATE, Recorder, StoryAgent, dump

pytestmark = pytest.mark.unit


class Noop:
    async def set_mom_grant(self, action: Any) -> None:
        return None

    async def save_transplant(self) -> None:
        return None

    async def reset(self) -> None:
        return None


def make_runner(
    rec: Recorder, agent: StoryAgent | None = None, reset_error: Exception | None = None
) -> tuple[Runner, list[tuple[str, str]], list[str]]:
    published: list[tuple[str, str]] = []
    resets: list[str] = []
    mock = httpx.AsyncClient(transport=rec.mock(), base_url="http://mock.test")
    control = Control(mock=mock, grants=Noop(), settings=Noop())

    async def reset() -> None:
        resets.append("seed")
        if reset_error:
            raise reset_error

    @asynccontextmanager
    async def agents() -> AsyncIterator[Callable[[str], Any]]:
        async def agent_for(user_id: str) -> StoryAgent:
            return agent or StoryAgent()

        yield agent_for

    runner = Runner(
        reset=reset,
        control=lambda: control,
        agents=agents,
        publish=lambda e, h: published.append((e, h)),
        agent_label="scripted",
    )
    return runner, published, resets


def test_plan_replays_everything_before() -> None:
    assert plan("moment-1") == (("moment-1",), ())
    assert plan("moment-3") == (("moment-1", "moment-2", "moment-3"), ("moment-1", "moment-2"))
    assert [m for m, _ in MACROS] == [s.name for s in STORIES]
    with pytest.raises(KeyError):
        plan("moment-4")


async def test_moment_2_resets_replays_moment_1_and_passes() -> None:
    rec = Recorder()
    runner, published, resets = make_runner(rec)
    result = await runner.run("moment-2")
    assert resets == ["seed"]
    assert result.error is None and result.failed == 0
    assert result.steps == len(STORIES[0].steps) + len(STORIES[1].steps)
    turns = [h for e, h in published if e == "turn"]
    assert len(turns) == result.steps
    assert all("replay" in h for h in turns[: len(STORIES[0].steps)])
    assert not any("replay" in h for h in turns[len(STORIES[0].steps) :])
    assert "/_admin/scenarios/load" not in dump(rec.requests)  # Reset is the seed, not run_demo's reload
    assert "2 steps" not in published[-1][1] and "0 mismatched" in published[-1][1]


async def test_moment_3_fires_by_ref_only() -> None:
    rec = Recorder()
    runner, _, _ = make_runner(rec)
    await runner.run("moment-3")
    fires = [r for r in rec.requests if r.url.path.endswith("/events")]
    assert fires and all(r.url.path == f"/_admin/lines/{MOM_REF}/events" for r in fires)
    assert "+1" not in dump(rec.requests)


async def test_mismatch_is_a_red_badge_and_the_run_goes_on() -> None:
    rec = Recorder()
    runner, published, _ = make_runner(rec, StoryAgent(wrong="Is my line OK?"))
    result = await runner.run("moment-1")
    assert result.failed == 1 and result.steps == len(STORIES[0].steps)
    bad = [h for e, h in published if e == "turn" and "FAIL" in h]
    assert len(bad) == 1 and "CARRIER_ERROR" in bad[0] and "expected" in bad[0]
    assert "1 mismatched" in published[-1][1]


async def test_reset_failure_stops_with_the_reason() -> None:
    from demo_ui.clients import Unavailable

    rec = Recorder()
    runner, published, _ = make_runner(rec, reset_error=Unavailable("Reset failed: SeedError: x"))
    result = await runner.run("moment-1")
    assert result.error and "Reset failed" in result.error
    assert "stopped" in published[-1][1] and not [e for e, _ in published if e == "turn"]


async def test_mock_down_stops_the_run() -> None:
    rec = Recorder(status=401)
    runner, published, _ = make_runner(rec)
    result = await runner.run("moment-1")
    assert result.error is not None and "stopped" in published[-1][1]


async def test_one_run_at_a_time() -> None:
    rec = Recorder()
    gate = asyncio.Event()
    runner, _, _ = make_runner(rec)
    original = runner.reset

    async def slow_reset() -> None:
        await gate.wait()
        await original()

    runner.reset = slow_reset
    task = runner.start("moment-1")
    with pytest.raises(Busy):
        runner.start("moment-2")
    gate.set()
    await task
    assert not runner.busy
    with pytest.raises(KeyError):
        runner.start("nope")


async def test_bind_line_next_step_becomes_a_qr_frame() -> None:
    rec = Recorder()
    agent = StoryAgent(next_step={"kind": "bind_line", "url": "http://localhost:8081/bind/abc"})
    runner, published, _ = make_runner(rec, agent)
    await runner.run("moment-1")
    qrs = [h for e, h in published if e == "qr"]
    assert qrs and "<svg" in qrs[0] and "as=phone-asish" in qrs[0] and "D1" in qrs[0]


# --- the sequencer, step 3 (doc 11 §5, §11): every macro, its order, its badges, its timing ----------------------

STEP_RE = re.compile(r'<article class="step step-(\w+)( replay)?( bad)?" id="step-([\w-]+)">')


def expected_steps(name: str) -> list[tuple[str, str, bool]]:
    """(step id, kind, replay) for the macro `name`: every story up to it on one timeline, earlier ones replayed."""
    kinds = {"Say": "say", "Advance": "advance", "Fire": "fire", "Grant": "grant", "Settings": "settings",
             "Note": "note"}  # fmt: skip
    upto = [s.name for s in STORIES][: [s.name for s in STORIES].index(name) + 1]
    return [
        (f"{st.name}-{n}", kinds[type(step).__name__], st.name != name)
        for st in STORIES
        if st.name in upto
        for n, step in enumerate(st.steps, start=1)
    ]


def fixed_perf_counter(monkeypatch: pytest.MonkeyPatch, step_s: float) -> None:
    """`run_demo` times each step with `time.perf_counter()`: a counter that moves `step_s` per read makes every
    step's `elapsed_ms` exact (testing.md rule 4: no wall clock)."""
    ticks = itertools.count()
    monkeypatch.setattr(demo_mod, "time", SimpleNamespace(perf_counter=lambda: next(ticks) * step_s))


@pytest.mark.parametrize("name", [m for m, _ in MACROS])
async def test_every_macro_is_reset_then_replay_then_its_own_story(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder()
    order: list[str] = []
    runner, published, resets = make_runner(rec)
    seed = runner.reset

    async def reset() -> None:
        order.append(f"reset@{len(rec.requests)}")  # how many mock calls had been made when Reset ran
        await seed()

    runner.reset = reset
    fixed_perf_counter(monkeypatch, 0.25)
    result = await runner.run(name)

    assert resets == ["seed"] and order == ["reset@0"]  # Reset first, once, before any mock call
    turns = [h for e, h in published if e == "turn"]
    got = [(m[4], m[1], bool(m[2])) for m in (STEP_RE.search(h) for h in turns) if m]
    assert len(got) == len(turns)
    assert got == expected_steps(name)  # the stories' steps, in order, earlier stories marked replay
    says = sum(1 for _, kind, _ in got if kind == "say")
    assert sum(h.count('class="badge pass"') for h in turns) == says
    assert not any("FAIL" in h for h in turns)
    assert all('<span class="ms">250 ms</span>' in h for h in turns)  # each step's own timing
    assert result == RunResult(name, steps=len(got), failed=0, error=None)
    statuses = [h for e, h in published if e == "status"]
    assert "Reset (make seed)" in statuses[0]
    assert f"running {', '.join(plan(name)[0])}" in statuses[1]
    assert 'class="status pass"' in statuses[-1] and f"{len(got)} steps, 0 mismatched" in statuses[-1]


async def test_badge_compares_codes_in_order() -> None:
    """Moment 2 expects SIM_SWAPPED_RECENT then CALL_FORWARDING_SET; the same codes reversed are a red badge."""

    class Reversed(StoryAgent):
        async def ask(self, utterance: str, *, expect: Any = None) -> Any:
            turn = await super().ask(utterance, expect=expect)
            assert turn.result is not None
            turn.result["reason_codes"] = list(reversed(turn.result["reason_codes"]))
            return turn

    runner, published, _ = make_runner(Recorder(), Reversed())
    result = await runner.run("moment-2")
    bad = [h for e, h in published if e == "turn" and 'class="badge fail"' in h]
    assert result.failed == 1 and len(bad) == 1
    assert "Is anything forwarding my calls?" in bad[0] and " bad" in bad[0]
    assert 'class="status fail"' in published[-1][1]


async def test_tower_error_stops_the_run_after_the_steps_it_reached() -> None:
    class Refused(StoryAgent):
        async def ask(self, utterance: str, *, expect: Any = None) -> Any:
            if utterance == "My phone just lost signal. Is my line OK?":
                raise TowerError("Tower answered 401")
            return await super().ask(utterance, expect=expect)

    runner, published, _ = make_runner(Recorder(), Refused())
    result = await runner.run("moment-1")
    assert result.error == "TowerError: Tower answered 401"
    assert [m[4] for m in (STEP_RE.search(h) for e, h in published if e == "turn") if m] == [
        "moment-1-1",
        "moment-1-2",
    ]
    assert 'class="status fail"' in published[-1][1] and "stopped" in published[-1][1]
    assert not runner.busy


async def test_a_scenario_without_mom_is_a_demo_error() -> None:
    rec = Recorder()
    runner, published, _ = make_runner(rec)
    no_mom = {**STATE, "lines": {k: v for k, v in STATE["lines"].items() if k != MOM_REF}}
    mock = rec.mock()

    def handle(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/_admin/state":
            rec.requests.append(r)
            return httpx.Response(200, json=no_mom)
        return mock.handle_request(r)

    runner.control = lambda: Control(
        mock=httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://mock.test"),
        grants=Noop(),
        settings=Noop(),
    )
    result = await runner.run("moment-1")
    assert result.error is not None and result.error.startswith("DemoError")
    assert "phone-mom" in result.error and not [e for e, _ in published if e == "turn"]


async def test_a_finished_or_failed_run_frees_the_runner() -> None:
    from demo_ui.clients import Unavailable

    runner, _, _ = make_runner(Recorder(), reset_error=Unavailable("Reset failed: x"))
    failed = await runner.start("moment-1")
    assert failed.error and not runner.busy
    runner2, _, _ = make_runner(Recorder())
    gate = asyncio.Event()
    inner = runner2.reset

    async def held() -> None:
        await gate.wait()
        await inner()

    runner2.reset = held
    first = runner2.start("transplant")
    with pytest.raises(Busy):
        runner2.start("transplant")  # the same macro twice is refused too
    gate.set()
    assert (await first).error is None
    assert (await runner2.start("moment-1")).error is None  # and the next one runs
