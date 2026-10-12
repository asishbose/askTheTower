"""09 §4 (human decision 2026-10-11, test_c23 root cause): an `Advance` with `expect` waits until Alerts' sent list
(`GET /internal/sent?after=<n>`, 06 §3.1) shows that text, read from the cursor at the story's start; poll 0.2 s,
ceiling 10 s, then the step fails visibly; no sent list → skipped and said so. Fakes only, a fake clock, no sleeping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import pytest
from ref_client.demo import (
    SENT_POLL_S,
    SENT_WAIT_S,
    STORIES,
    Advance,
    AlertsSentList,
    Control,
    ExpectSent,
    Say,
    SentListUnavailable,
    SentWatch,
    StepReport,
    alerts_sent_list,
    run_demo,
)
from ref_client.transcript import ToolCall, Turn

pytestmark = pytest.mark.unit

PARTNER = ExpectSent("escalation[0]", "user-partner")
NEIGHBOUR = ExpectSent("escalation[1]", "user-neighbour")


@dataclass
class FakeClock:
    t: float = 0.0
    slept: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


@dataclass
class _Pending:
    left: int
    role: str
    user_id: str


class FakeSent:
    """A sent list whose queued texts become visible on the (polls+1)-th read after `emit`."""

    def __init__(self, existing: int = 0, *, down: bool = False) -> None:
        self.log: list[dict[str, object]] = [
            {"n": i, "role": "watcher", "user_id": "user-asish", "body": "b"} for i in range(1, existing + 1)
        ]
        self.pending: list[_Pending] = []
        self.asked: list[int] = []
        self.down = down

    def emit(self, want: ExpectSent, *, polls: int = 0) -> None:
        self.pending.append(_Pending(polls, want.role, want.user_id))

    async def after(self, n: int) -> list[dict[str, object]]:
        self.asked.append(n)
        if self.down:
            raise SentListUnavailable("Alerts' sent list answered 401")
        for p in list(self.pending):
            if p.left == 0:
                self.pending.remove(p)
                self.log.append({"n": len(self.log) + 1, "role": p.role, "user_id": p.user_id, "body": "b"})
            else:
                p.left -= 1
        return [e for e in self.log if int(str(e["n"])) > n]


async def _watch(sent: FakeSent | None, clock: FakeClock) -> SentWatch:
    watch = SentWatch(sent, clock.sleep, clock.monotonic)
    await watch.start()
    return watch


# --- the wait ------------------------------------------------------------------------------------------------


async def test_waits_until_the_text_appears_then_proceeds() -> None:
    clock, sent = FakeClock(), FakeSent(existing=3)
    watch = await _watch(sent, clock)
    assert watch.cursor == 3
    sent.emit(PARTNER, polls=4)
    check = await watch.wait_for(PARTNER)
    assert check.status == "seen" and "escalation[0] text to user-partner" in check.text
    assert clock.slept == [SENT_POLL_S] * 4
    assert set(sent.asked[1:]) == {3}, "every read is by the cursor taken at the story's start"


async def test_never_appears_fails_at_the_ceiling_without_hanging() -> None:
    clock, sent = FakeClock(), FakeSent()
    watch = await _watch(sent, clock)
    sent.emit(PARTNER)  # the wrong text arrives; the partner's never does
    check = await watch.wait_for(NEIGHBOUR)
    assert check.status == "missing"
    assert "no escalation[1] text to user-neighbour" in check.text and "10 s" in check.text
    assert SENT_WAIT_S <= clock.t < SENT_WAIT_S + 2 * SENT_POLL_S
    assert all(s == SENT_POLL_S for s in clock.slept)


async def test_a_text_from_before_the_story_does_not_count() -> None:
    clock, sent = FakeClock(), FakeSent()
    sent.emit(PARTNER)
    await sent.after(0)  # published before the story starts
    watch = await _watch(sent, clock)
    assert watch.cursor == 1
    assert (await watch.wait_for(PARTNER)).status == "missing"


async def test_one_entry_satisfies_one_wait() -> None:
    clock, sent = FakeClock(), FakeSent()
    watch = await _watch(sent, clock)
    sent.emit(PARTNER)
    assert (await watch.wait_for(PARTNER)).status == "seen"
    assert (await watch.wait_for(PARTNER)).status == "missing"


async def test_the_cursor_pages_past_200_entries() -> None:
    clock = FakeClock()

    class Paged(FakeSent):
        async def after(self, n: int) -> list[dict[str, object]]:
            return (await super().after(n))[:200]

    sent = Paged(existing=450)
    watch = await _watch(sent, clock)
    assert watch.cursor == 450
    assert sent.asked == [0, 200, 400, 450]


async def test_no_sent_list_skips_and_says_so() -> None:
    clock = FakeClock()
    check = await (await _watch(None, clock)).wait_for(PARTNER)
    assert check.status == "skipped" and "no sent list here" in check.text
    assert clock.slept == []


async def test_an_unreachable_sent_list_skips_and_says_why() -> None:
    clock = FakeClock()
    check = await (await _watch(FakeSent(down=True), clock)).wait_for(PARTNER)
    assert check.status == "skipped" and "401" in check.text
    assert clock.slept == []


async def test_a_sent_list_lost_mid_story_skips_rather_than_fails() -> None:
    clock, sent = FakeClock(), FakeSent()
    watch = await _watch(sent, clock)
    sent.down = True
    assert (await watch.wait_for(PARTNER)).status == "skipped"


# --- the story ---------------------------------------------------------------------------------------------


def test_only_the_transplant_jumps_expect_a_text() -> None:
    expects = {
        (story.name, step.minutes, step.expect)
        for story in STORIES
        for step in story.steps
        if isinstance(step, Advance) and step.expect is not None
    }
    assert expects == {("transplant", 20, PARTNER), ("transplant", 15, NEIGHBOUR)}


REFS = {
    "line:aaaaaaaaaaaaaaaa": {"mobile_data_client_ids": ["phone-asish"]},
    "line:bbbbbbbbbbbbbbbb": {"mobile_data_client_ids": ["phone-mom"]},
}


class FakeAgent:
    name = "scripted"
    model_id: str | None = None

    async def ask(self, utterance: str, *, expect: ToolCall | None = None) -> Turn:
        codes = next(
            s.codes for st in STORIES for s in st.steps if isinstance(s, Say) and s.text == utterance
        )
        result = {"summary": "s", "reason_codes": list(codes)}
        return Turn(utterance, [expect] if expect else [], result, "s", [result])


class Noop:
    async def set_mom_grant(self, action: Literal["revoke", "grant"]) -> None:
        return None

    async def save_transplant(self) -> None:
        return None

    async def reset(self) -> None:
        return None


async def _transplant(
    sent: FakeSent | None, emits: dict[int, tuple[ExpectSent, int]]
) -> tuple[list[StepReport], Any, list[str], FakeClock]:
    """Run the transplant story; a +m jump makes the fake sent list publish `emits[m]` after its polls."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_admin/state":
            return httpx.Response(200, json={"lines": REFS})
        if request.url.path == "/_admin/clock":
            minutes = int(json.loads(request.content)["advance_s"]) // 60
            if sent is not None and minutes in emits:
                want, polls = emits[minutes]
                sent.emit(want, polls=polls)
        return httpx.Response(200, json={"fired": []})

    clock = FakeClock()
    mock = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://mock.test")
    control = Control(
        mock=mock, grants=Noop(), settings=Noop(), sent=sent, sleep=clock.sleep, monotonic=clock.monotonic
    )
    reports: list[StepReport] = []
    lines: list[str] = []

    async def agent_for(user_id: str) -> FakeAgent:
        return FakeAgent()

    run = await run_demo(
        agent_for, control, reset=False, stories=("transplant",), echo=lines.append, on_step=reports.append
    )
    await mock.aclose()
    return reports, run, lines, clock


def _advances(reports: list[StepReport]) -> list[StepReport]:
    return [r for r in reports if r.kind == "advance"]


async def test_story_waits_for_the_partner_then_the_neighbour() -> None:
    sent = FakeSent(existing=2)
    reports, run, lines, clock = await _transplant(sent, {20: (PARTNER, 3), 15: (NEIGHBOUR, 1)})
    assert run.mismatches == []
    adv = _advances(reports)
    assert [r.ok for r in adv] == [True, True]
    assert "escalation[0] text to user-partner" in adv[0].sent
    assert "escalation[1] text to user-neighbour" in adv[1].sent
    assert clock.slept == [SENT_POLL_S] * 4
    assert sent.asked[:2] == [0, 2], "the cursor: the list's end when the story starts"
    assert set(sent.asked[1:]) == {2}, "the waits read only by that cursor"
    assert sum("[alerts]" in ln for ln in lines) == 2


async def test_story_fails_visibly_when_the_neighbour_never_comes() -> None:
    reports, run, lines, clock = await _transplant(FakeSent(), {20: (PARTNER, 0)})
    adv = _advances(reports)
    assert [r.ok for r in adv] == [True, False]
    assert "MISSING" in adv[1].narration and "user-neighbour" in adv[1].narration
    assert len(run.mismatches) == 1 and "escalation[1] text to user-neighbour" in run.mismatches[0]
    assert any(ln.startswith("  [alerts] MISSING:") for ln in lines)
    says = [r for r in reports if r.kind == "say"]
    assert all(r.ok for r in says), "the story still runs to its end"


async def test_story_without_a_sent_list_skips_the_waits_and_says_so() -> None:
    reports, run, lines, clock = await _transplant(None, {})
    adv = _advances(reports)
    assert [r.ok for r in adv] == [None, None]
    assert all("skipped" in r.sent for r in adv)
    assert run.mismatches == [] and clock.slept == []
    assert sum("skipped" in ln for ln in lines) == 2


async def test_the_wait_is_not_a_transcript_step() -> None:
    _, waited, _, _ = await _transplant(FakeSent(), {20: (PARTNER, 2), 15: (NEIGHBOUR, 2)})
    _, skipped, _, _ = await _transplant(None, {})
    assert waited.transcripts[0].steps == skipped.transcripts[0].steps


async def test_other_stories_never_read_the_sent_list() -> None:
    sent = FakeSent()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/_admin/state":
            return httpx.Response(200, json={"lines": REFS})
        return httpx.Response(200, json={"fired": []})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://mock.test")
    control = Control(mock=mock, grants=Noop(), settings=Noop(), sent=sent)

    async def agent_for(user_id: str) -> FakeAgent:
        return FakeAgent()

    await run_demo(
        agent_for, control, reset=False, stories=("moment-1", "moment-2", "moment-3"), echo=lambda _s: None
    )
    await mock.aclose()
    assert sent.asked == []


# --- the HTTP sent list and its composition -----------------------------------------------------------------


async def test_http_sent_list_contract() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"sent": [{"n": 5, "role": "escalation[0]", "user_id": "user-partner"}]}
        )

    s = AlertsSentList("http://alerts.test/", "tok", transport=httpx.MockTransport(handle))
    assert await s.after(4) == [{"n": 5, "role": "escalation[0]", "user_id": "user-partner"}]
    await s.aclose()
    assert seen[0].url.path == "/internal/sent" and seen[0].url.params["after"] == "4"
    assert seen[0].headers["authorization"] == "Bearer tok"


@pytest.mark.parametrize(("status", "why"), [(401, "401"), (404, "off here"), (503, "503")])
async def test_http_sent_list_failures_are_unavailable(status: int, why: str) -> None:
    s = AlertsSentList(
        "http://alerts.test", "tok", transport=httpx.MockTransport(lambda _r: httpx.Response(status))
    )
    with pytest.raises(SentListUnavailable, match=why):
        await s.after(0)
    await s.aclose()


async def test_http_sent_list_unreachable_is_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    s = AlertsSentList("http://alerts.test", "tok", transport=httpx.MockTransport(refuse))
    with pytest.raises(SentListUnavailable, match="not reachable"):
        await s.after(0)
    await s.aclose()


@pytest.mark.parametrize(
    ("env", "environ", "built"),
    [
        ("local", {"ALERTS_INTERNAL_BEARER": "t"}, True),
        ("local", {}, False),
        ("eks", {}, False),
        ("aws", {"ALERTS_INTERNAL_BEARER": "t"}, False),
        ("aws", {"DEMO_ADMIN_BEARER": "d", "ALERTS_URL": "https://alerts.example"}, True),
    ],
)
async def test_sent_list_from_the_environment(env: str, environ: dict[str, str], built: bool) -> None:
    s = alerts_sent_list(env, environ)
    assert (s is not None) == built
    if s is not None:
        await s.aclose()
