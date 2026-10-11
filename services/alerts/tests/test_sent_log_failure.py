"""06 §3.1, §8 (human decision 2 of 2026-10-11): the sent-SMS ledger write fails after a delivered SMS.

Log and continue: one log line (role and template, never the number or the body), the `sent_log_failed`
counter, one audit row `ALERT_LOG_FAILED` (outcome `ok`, the SMS's template as `message_ref`), no re-send, and the
invocation succeeds. The alert itself still counts as sent (its `changed` row, `last_alert_at`, the rate limit).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import pytest
from alerts import handler
from alerts import state as S
from alerts.runner import process_line
from alerts.send_backends import LogSender
from alerts.sent_log import MAX_ENTRIES, SentEntry
from alerts.testing import MOM, T0, World, audit_rows, row_summary
from tower_consent import Store, get_watch

from tests.privacy.patterns import HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.integration

M = timedelta(minutes=1)
CHANGED = ("event", "changed", ("SIM_SWAPPED_RECENT",), "SIM_SWAPPED_RECENT.sms")
LOG_FAILED = ("event", "ok", ("ALERT_LOG_FAILED",), "SIM_SWAPPED_RECENT.sms")


class BrokenSentLog:
    """A ledger whose store is down. The error text carries the body, so a log of it would leak it."""

    def __init__(self) -> None:
        self.calls = 0

    def record(self, *, at: datetime, template: str, role: str, user_id: str, body: str) -> SentEntry:
        self.calls += 1
        raise RuntimeError(f"SentSms put_item failed for {body!r}")

    def after(self, n: int, limit: int = MAX_ENTRIES) -> list[SentEntry]:
        return []


class AuditCheckingSender(LogSender):
    """Records how many audit rows existed when each SMS left: audit-before-send (e2e §8.5)."""

    def __init__(self, store: Store, line_id: str) -> None:
        super().__init__()
        self.store, self.line_id = store, line_id
        self.rows_before = len(audit_rows(store, line_id))
        self.rows_at_send: list[int] = []

    async def send(self, to_e164: str, body: str, *, label: str) -> None:
        self.rows_at_send.append(len(audit_rows(self.store, self.line_id)))
        await super().send(to_e164, body, label=label)


def _break_ledger(world: World, mom: str) -> tuple[BrokenSentLog, AuditCheckingSender]:
    broken, sender = BrokenSentLog(), AuditCheckingSender(world.store, mom)
    world.svc.sent_log = broken
    world.svc.sender = sender
    return broken, sender


def _assert_sent_once_never_to_the_swapped_line(sender: AuditCheckingSender) -> None:
    assert [s.label for s in sender.sent] == ["chain:user-asish"]
    assert all(s.to_e164 != MOM for s in sender.sent)  # never text the swapped line
    assert sender.rows_at_send == [sender.rows_before + 1]  # the `changed` row existed before the SMS left
    for s in sender.sent:
        assert not phone_hits(s.body) and not HEALTH_WORDS.search(s.body)


def _assert_logs_clean(caplog: pytest.LogCaptureFixture, body: str) -> None:
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert (
        "sent-SMS ledger write failed to=chain:user-asish ref=SIM_SWAPPED_RECENT.sms error=RuntimeError"
        in text
    )
    failed = [r.getMessage() for r in caplog.records if "ledger write failed" in r.getMessage()]
    assert all(body not in m and "put_item failed" not in m for m in failed)
    assert phone_hits(text) == []


async def test_a_failed_ledger_write_is_audited_and_the_sms_is_not_resent(
    world: World, mom: str, carrier, clock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    await process_line(world.svc, mom, "poll", T0)
    broken, sender = _break_ledger(world, mom)
    clock.set(T0 + M)
    carrier.swap(MOM)

    ((decision, result),) = await process_line(world.svc, mom, "event", clock.at)

    _assert_sent_once_never_to_the_swapped_line(sender)
    assert broken.calls == 1  # recorded once, never retried, and the SMS is not re-sent
    assert row_summary(audit_rows(world.store, mom))[sender.rows_before :] == [CHANGED, LOG_FAILED]
    assert world.svc.metrics["sent_log_failed"] == 1 and world.svc.metrics["sms_sent"] == 1
    assert world.svc.metrics["sms_failed"] == 0
    # the alert still counts as sent: last_alert_at moved, so the 6 h rate limit holds
    assert result is not None and [c.value for c in result.sent_codes] == ["SIM_SWAPPED_RECENT"]
    state = get_watch(world.store, mom, "user-asish").last_state
    assert state is not None and state.last_alert_at["SIM_SWAPPED_RECENT"] == clock.at
    _assert_logs_clean(caplog, sender.sent[0].body)


def test_the_lambda_invocation_succeeds_when_the_ledger_write_fails(
    world: World, mom: str, carrier, clock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    assert handler.lambda_handler({"profile": "care"}, None, svc=world.svc)["alerts"] == 0  # baseline
    token = S.ensure_sink_token(world.store, mom).sink_token
    assert token
    broken, sender = _break_ledger(world, mom)
    clock.set(T0 + M)
    carrier.swap(MOM)
    event = {
        "id": "evt-ledger-1",
        "source": "https://mock.test/sim-swap-subscriptions/v0.2/subscriptions/sub-0001",
        "type": "org.camaraproject.sim-swap-subscriptions.v0.swapped",
        "specversion": "1.0",
        "datacontenttype": "application/json",
        "time": "2026-10-05T14:01:00Z",
        "data": {"subscriptionId": "sub-0001"},
    }
    gateway = {
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": f"/hooks/sim-swap/{token}",
        "headers": {"content-type": "application/json"},
        "body": json.dumps(event),
        "isBase64Encoded": False,
    }

    r = handler.lambda_handler(gateway, None, svc=world.svc)

    assert r["statusCode"] == 200, r
    _assert_sent_once_never_to_the_swapped_line(sender)
    assert broken.calls == 1
    assert row_summary(audit_rows(world.store, mom))[sender.rows_before :] == [CHANGED, LOG_FAILED]
    assert world.svc.metrics["sent_log_failed"] == 1
    _assert_logs_clean(caplog, sender.sent[0].body)
