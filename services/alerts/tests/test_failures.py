"""06 §8 failure table: carrier misses, SMS failures, expired subscriptions, duplicates (see test_hooks)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from alerts import state as S
from alerts.runner import poll, process_line
from alerts.send_backends import SendError, SnsSender
from alerts.testing import MOM, T0, World, audit_rows, row_summary
from tower_consent import get_watch

pytestmark = pytest.mark.integration
M = timedelta(minutes=1)


async def test_three_poll_misses_audit_carrier_error_and_never_alert(
    world: World, mom: str, carrier, clock, sender
) -> None:
    await process_line(world.svc, mom, "poll", T0)
    before = get_watch(world.store, mom, "user-asish").last_state
    carrier.fail["*"] = -1
    carrier.swap(MOM)  # a change exists, but we cannot observe it
    carrier.set_reachable(MOM, False)
    for i in range(1, 6):
        clock.set(T0 + i * 30 * M)
        await process_line(world.svc, mom, "poll", clock.at)
        if i == 2:
            assert audit_rows(world.store, mom) == []
    assert sender.sent == []
    assert row_summary(audit_rows(world.store, mom)) == [("poll", "refused", ("CARRIER_ERROR",), None)]
    assert get_watch(world.store, mom, "user-asish").last_state == before  # kept, not overwritten
    assert S.get_line_row(world.store, mom).misses == 5

    carrier.fail.clear()  # recovery: the next observation counts again, and the change is reported
    clock.set(T0 + 200 * M)
    await process_line(world.svc, mom, "poll", clock.at)
    assert S.get_line_row(world.store, mom).misses == 0
    assert [s.label for s in sender.sent] == ["chain:user-asish"]


async def test_sms_failure_retries_once(world: World, carrier, clock, sender) -> None:
    from alerts.testing import ASISH, FakeLine

    world.standard()
    world.watch(MOM, "user-asish", "care", [("user-asish", False), ("user-neighbour", False)])
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60))
    carrier.lines[ASISH] = FakeLine()
    line = world.lines[MOM]
    await process_line(world.svc, line, "poll", T0)
    sender.fail_next = 1
    clock.set(T0 + M)
    carrier.swap(MOM)
    await process_line(world.svc, line, "event", clock.at)
    assert [s.label for s in sender.sent] == ["chain:user-asish"]
    assert [r[1] for r in row_summary(audit_rows(world.store, line))] == ["changed"]


async def test_sms_fails_twice_alert_failed_then_next_recipient(world: World, carrier, clock, sender) -> None:
    from alerts.testing import ASISH, FakeLine

    world.standard()
    world.watch(MOM, "user-asish", "care", [("user-asish", False), ("user-neighbour", False)])
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60))
    carrier.lines[ASISH] = FakeLine()
    line = world.lines[MOM]
    await process_line(world.svc, line, "poll", T0)
    sender.fail_next = 2
    clock.set(T0 + M)
    carrier.swap(MOM)
    await process_line(world.svc, line, "event", clock.at)
    assert [s.label for s in sender.sent] == ["chain:user-neighbour"]
    assert row_summary(audit_rows(world.store, line)) == [
        ("event", "changed", ("SIM_SWAPPED_RECENT",), "SIM_SWAPPED_RECENT.sms"),
        ("event", "suppressed", ("ALERT_FAILED",), "SIM_SWAPPED_RECENT.sms"),
    ]


async def test_expired_subscription_is_renewed_by_the_poller(world: World, mom: str, carrier, clock) -> None:
    from alerts.subscriptions import subscribe

    ids = await subscribe(world.svc, mom, None, T0)
    assert len(ids) == 4  # sim-swap + three reachability types (care)
    clock.set(T0 + timedelta(days=14))
    await poll(world.svc, "care", clock.at)
    assert S.get_line_row(world.store, mom).subs_at == T0  # within TTL/2: untouched
    clock.set(T0 + timedelta(days=15, minutes=1))
    await poll(world.svc, "care", clock.at)
    row = S.get_line_row(world.store, mom)
    assert (
        row.subs_at == clock.at and len(row.subscription_ids) == 4 and set(row.subscription_ids) != set(ids)
    )
    assert ("poll", "ok", ("OK",), None) in row_summary(audit_rows(world.store, mom))
    assert get_watch(world.store, mom, "user-asish").subscription_ids == row.subscription_ids


async def test_sns_sender_on_moto(moto_aws) -> None:
    import boto3
    from moto.core import DEFAULT_ACCOUNT_ID
    from moto.sns.models import sns_backends

    sns = boto3.client("sns", region_name="us-east-1")
    await SnsSender(sns).send(MOM, "SIM moved to another device at 10:01 today.", label="chain:user-asish")
    sent = sns_backends[DEFAULT_ACCOUNT_ID]["us-east-1"].sms_messages
    assert any(v == (MOM, "SIM moved to another device at 10:01 today.") for v in sent.values())


async def test_sns_failure_carries_no_number() -> None:
    class Boom:
        def publish(self, **kw: object) -> None:
            raise RuntimeError(f"Invalid parameter: PhoneNumber {kw['PhoneNumber']}")

    with pytest.raises(SendError) as exc:
        await SnsSender(Boom()).send(MOM, "x", label="chain:user-asish")
    assert MOM not in str(exc.value) and exc.value.__cause__ is None
