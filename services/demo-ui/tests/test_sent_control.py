"""09 §4 / doc 11 §5: the macros' sent-list wait reads the feed's `AlertsSent` (same client, same bearer); a failure
of that client skips the wait rather than stopping the macro."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from demo_ui.clients import AlertsSent, AlertsSentControl, MockAdmin
from demo_ui.config import Settings
from demo_ui.deps import build_control
from ref_client.demo import SentListUnavailable

from .fakes import Recorder

pytestmark = pytest.mark.unit


def _alerts(rec: Recorder) -> AlertsSent:
    return AlertsSent(httpx.AsyncClient(transport=rec.alerts(), base_url="http://alerts.test"))


async def test_the_control_reads_the_feeds_client_by_cursor() -> None:
    rec = Recorder(sent=[{"n": 1, "role": "escalation[0]", "user_id": "user-partner", "body": "b"}])
    alerts = _alerts(rec)
    assert [e["n"] for e in await AlertsSentControl(alerts).after(0)] == [1]
    assert await AlertsSentControl(alerts).after(1) == []
    assert [r.url.params["after"] for r in rec.requests] == ["0", "1"]
    await alerts.http.aclose()


async def test_an_unavailable_feed_client_is_sent_list_unavailable() -> None:
    rec = Recorder(status=401)
    alerts = _alerts(rec)
    with pytest.raises(SentListUnavailable, match="401"):
        await AlertsSentControl(alerts).after(0)
    await alerts.http.aclose()


async def test_build_control_wires_the_sent_list_when_alerts_is_there() -> None:
    closers: list[Callable[[], Awaitable[None]]] = []
    mock = MockAdmin(httpx.AsyncClient(base_url="http://mock.test"))
    alerts = _alerts(Recorder())
    with_sent = build_control(Settings(), mock, None, {}, closers, alerts)
    without = build_control(Settings(), mock, None, {}, closers, None)
    assert with_sent is not None and isinstance(with_sent.sent, AlertsSentControl)
    assert without is not None and without.sent is None
    for close in closers:
        await close()
    await mock.http.aclose()
    await alerts.http.aclose()
