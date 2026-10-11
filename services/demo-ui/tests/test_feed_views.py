"""Doc 11 §4 (one poller, push on change, snapshot on connect) and the fragments: chips only from Tower's codes,
escaping, the QR code, the number filter on the way out."""

from __future__ import annotations

import httpx
import pytest
from demo_ui import views
from demo_ui.app import sse_frames
from demo_ui.clients import AlertsSent, MockAdmin
from demo_ui.feed import CarrierSource, Feed, Frame, SmsSource, carrier_view, short_ref
from ref_client.demo import StepReport
from ref_client.transcript import ToolCall, Turn

from tests.privacy.patterns import phone_hits

from .fakes import ASISH_REF, STATE, Recorder

pytestmark = pytest.mark.unit


class Counting:
    event = "x"

    def __init__(self) -> None:
        self.calls = 0
        self.html = "<p>a</p>"

    async def render(self) -> str:
        self.calls += 1
        return self.html


async def test_feed_pushes_only_changes_and_snapshots_new_pages() -> None:
    src = Counting()
    feed = Feed([src], poll_s=1)
    q1, q2 = feed.subscribe(), feed.subscribe()
    assert await feed.tick() == 1
    assert await feed.tick() == 0  # unchanged: nothing pushed
    src.html = "<p>b</p>"
    await feed.tick()
    assert src.calls == 3  # one render per tick, however many pages are open
    assert [q1.get_nowait().html for _ in range(2)] == ["<p>a</p>", "<p>b</p>"]
    assert q2.qsize() == 2
    q3 = feed.subscribe()
    assert q3.get_nowait() == Frame("x", "<p>b</p>")  # the current fragment at once
    feed.unsubscribe(q1)
    feed.publish("turn", "<p>t</p>")
    assert q1.empty() and q2.qsize() == 3


def test_frame_is_valid_sse_for_multiline_html() -> None:
    assert Frame("audit", "<a>\n<b>").sse() == "event: audit\ndata: <a>\ndata: <b>\n\n"


async def test_sse_stream_sends_snapshot_then_keepalive_and_unsubscribes() -> None:
    feed = Feed([], poll_s=1)
    feed.publish("x", "ignored: no page yet")
    feed.last["carrier"] = "<p>c</p>"
    gen = sse_frames(feed, keepalive_s=0.01)
    assert await anext(gen) == "event: carrier\ndata: <p>c</p>\n\n"
    assert await anext(gen) == ": keepalive\n\n"
    assert len(feed.subscribers) == 1
    await gen.aclose()
    assert feed.subscribers == set()


def test_carrier_view_by_holder_and_short_ref() -> None:
    v = carrier_view(STATE)
    assert [line["holder"] for line in v["lines"]] == ["Asish", "Mom"]
    assert v["lines"][0]["ref"] == short_ref(ASISH_REF) == "line:1111aaaa"
    assert v["subscriptions"][0]["events"] == 1


async def test_carrier_source_renders_and_degrades() -> None:
    rec = Recorder()
    mock = MockAdmin(httpx.AsyncClient(transport=rec.mock(), base_url="http://m"))
    html = await CarrierSource(mock).render()
    assert "Asish" in html and "2026-10-05T14:00:00Z" in html
    assert rec.requests[0].url.params["view"] == "refs"
    rec.status = 401
    assert "401" in await CarrierSource(mock).render()
    assert "local-only" in await CarrierSource(None, "ENV=aws: local-only").render()


async def test_sms_source_asks_after_the_last_seen() -> None:
    rec = Recorder()
    rec.sent = [{"n": 1, "at": "t", "template": "SIM_SWAPPED_RECENT.sms", "role": "watcher",
                 "user_id": "user-asish", "body": "mom's SIM moved"}]  # fmt: skip
    src = SmsSource(AlertsSent(httpx.AsyncClient(transport=rec.alerts(), base_url="http://a")))
    assert "watcher" in await src.render()
    rec.sent.append({**rec.sent[0], "n": 2, "role": "escalation[0]"})
    html = await src.render()
    assert [r.url.params["after"] for r in rec.requests] == ["0", "1"]
    assert html.index("escalation[0]") < html.index("watcher")  # newest first
    rec.status = 404
    assert "SMS list unavailable" in await src.render()


def _say(codes: list[str], expected: tuple[str, ...], utterance: str = "Is my line OK?") -> StepReport:
    result = {"summary": "s", "reason_codes": codes}
    turn = Turn(utterance, [ToolCall("line_is_ok", {"line": "self"})], result, "s", [result])
    return StepReport("moment-1", "moment-1#1", "say", utterance, 12, expected, tuple(codes),
                      tuple(codes) == expected, False, (), turn)  # fmt: skip


def test_chips_come_from_tower_codes_only() -> None:
    html = views.step_fragment(_say(["OK"], ("OK",)), agent="scripted")
    assert "pass" in html and html.count('class="chip"') == 1 and "line_is_ok(line=self)" in html
    bad = views.step_fragment(_say(["NO_CONSENT"], ("OK",)), agent="scripted")
    assert "FAIL" in bad and "expected" in bad and "NO_CONSENT" in bad


def test_fragments_escape_and_mask() -> None:
    html = views.step_fragment(_say(["OK"], ("OK",), "<script>x</script> call +16135550101"), agent="s")
    assert "<script>" not in html and phone_hits(html) == []


def test_qr_fragment_and_with_phone() -> None:
    url = views.with_phone("http://localhost:8081/bind/tok", "mom", local=True)
    assert url.endswith("?as=phone-mom")
    assert views.with_phone("https://bind.example/bind/t?x=1", "mom", local=True).endswith("x=1&as=phone-mom")
    assert (
        views.with_phone("https://bind.example/bind/t", "mom", local=False) == "https://bind.example/bind/t"
    )
    html = views.bind_fragment(url, "mom")
    assert "<svg" in html and "D1" in html and "Mom" in html
