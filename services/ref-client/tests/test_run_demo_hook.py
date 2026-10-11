"""G4 (doc 11 §5, 09 §4): `run_demo(reset=False, on_step=…)` reports every step with its id, narration,
expected vs actual codes and timing; `reset=False` reads the line refs without reloading the scenario, and the
mock admin token goes on the control calls (G1). Fakes only."""

from __future__ import annotations

import json
from typing import Any, Literal

import httpx
import pytest
from ref_client.demo import STORIES, Control, StepReport, mock_admin_headers, run_demo
from ref_client.transcript import ToolCall, Turn

pytestmark = pytest.mark.unit

REFS = {
    "line:aaaaaaaaaaaaaaaa": {"mobile_data_client_ids": ["phone-asish"]},
    "line:bbbbbbbbbbbbbbbb": {"mobile_data_client_ids": ["phone-mom"]},
}


class FakeAgent:
    name = "scripted"
    model_id: str | None = None

    def __init__(self, wrong: str | None = None) -> None:
        self.wrong = wrong

    async def ask(self, utterance: str, *, expect: ToolCall | None = None) -> Turn:
        codes = next(s.codes for st in STORIES for s in st.steps if getattr(s, "text", None) == utterance)
        if utterance == self.wrong:
            codes = ("OK",) if codes != ("OK",) else ("NO_CONSENT",)
        result = {"summary": "s", "reason_codes": list(codes)}
        return Turn(utterance, [expect] if expect else [], result, "s", [result])


class Noop:
    async def set_mom_grant(self, action: Literal["revoke", "grant"]) -> None:
        return None

    async def save_transplant(self) -> None:
        return None

    async def reset(self) -> None:
        return None


def mock_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/_admin/state":
            assert request.url.params["view"] == "refs"
            return httpx.Response(200, json={"lines": REFS})
        if request.url.path == "/_admin/clock":
            return httpx.Response(200, json={"clock": "x", "fired": [{"event": "sim_swap"}]})
        return httpx.Response(200, json={})

    return httpx.MockTransport(handle)


async def _run(wrong: str | None = None, **kw: Any) -> tuple[list[StepReport], list[httpx.Request], Any]:
    seen: list[httpx.Request] = []
    mock = httpx.AsyncClient(
        transport=mock_transport(seen), base_url="http://mock.test", headers=mock_admin_headers("tok")
    )
    control = Control(mock=mock, grants=Noop(), settings=Noop())
    reports: list[StepReport] = []

    async def agent_for(user_id: str) -> FakeAgent:
        return FakeAgent(wrong)

    run = await run_demo(agent_for, control, echo=lambda _s: None, on_step=reports.append, **kw)
    await mock.aclose()
    return reports, seen, run


async def test_every_step_reported_in_order() -> None:
    reports, seen, run = await _run(reset=False, stories=("moment-1", "moment-2"), replay=("moment-1",))
    want = [(st.name, n) for st in STORIES[:2] for n in range(1, len(st.steps) + 1)]
    assert [(r.story, int(r.step_id.split("#")[1])) for r in reports] == want
    assert all(r.replay == (r.story == "moment-1") for r in reports)
    says = [r for r in reports if r.kind == "say"]
    assert [r.expected for r in says] == [r.actual for r in says]
    assert all(r.ok and r.turn is not None for r in says)
    advance = next(r for r in reports if r.kind == "advance")
    assert advance.fired == ("sim_swap",) and "fired: sim_swap" in advance.narration
    assert all(r.elapsed_ms >= 0 for r in reports)
    assert run.mismatches == []


async def test_reset_false_does_not_reload_and_fires_by_ref_with_the_token() -> None:
    _, seen, _ = await _run(reset=False, stories=("moment-3",))
    paths = [r.url.path for r in seen]
    assert "/_admin/scenarios/load" not in paths
    fires = [r for r in seen if r.url.path.endswith("/events")]
    assert fires and all(r.url.path == "/_admin/lines/line:bbbbbbbbbbbbbbbb/events" for r in fires)
    assert all(r.headers["authorization"] == "Bearer tok" for r in seen)
    assert all("+1" not in json.dumps(json.loads(r.content or b"{}")) for r in seen)


async def test_reset_true_reloads() -> None:
    _, seen, _ = await _run(stories=("moment-1",))
    assert seen[0].url.path == "/_admin/scenarios/load"


async def test_mismatch_is_reported_not_hidden() -> None:
    reports, _, run = await _run(wrong="Is my line OK?", reset=False, stories=("moment-1",))
    bad = [r for r in reports if r.ok is False]
    assert len(bad) == 1 and bad[0].expected == ("OK",) and bad[0].actual == ("NO_CONSENT",)
    assert len(run.mismatches) == 1


async def test_async_on_step_is_awaited() -> None:
    got: list[str] = []

    async def on_step(r: StepReport) -> None:
        got.append(r.step_id)

    seen: list[httpx.Request] = []
    mock = httpx.AsyncClient(transport=mock_transport(seen), base_url="http://mock.test")
    control = Control(mock=mock, grants=Noop(), settings=Noop())

    async def agent_for(user_id: str) -> FakeAgent:
        return FakeAgent()

    await run_demo(
        agent_for, control, echo=lambda _s: None, reset=False, stories=("moment-1",), on_step=on_step
    )
    await mock.aclose()
    assert got == ["moment-1#1", "moment-1#2", "moment-1#3"]


def test_admin_headers_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOCK_ADMIN_TOKEN", raising=False)
    assert mock_admin_headers() == {}
    monkeypatch.setenv("MOCK_ADMIN_TOKEN", "t")
    assert mock_admin_headers() == {"Authorization": "Bearer t"}
