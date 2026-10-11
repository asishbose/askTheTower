"""06 §5: at most one alert per line per reason per 6 h; repeats go to the audit, not the phone."""

from __future__ import annotations

from datetime import timedelta

import pytest
from alerts.runner import process_line
from alerts.testing import MOM, T0, World, audit_rows, row_summary

pytestmark = pytest.mark.integration

SENT = ("event", "changed", ("SIM_SWAPPED_RECENT",), "SIM_SWAPPED_RECENT.sms")
LIMITED = ("event", "suppressed", ("SIM_SWAPPED_RECENT",), None)


async def test_second_identical_change_within_6h_is_audited_not_sent(
    world: World, mom: str, carrier, clock, sender
) -> None:
    await process_line(world.svc, mom, "poll", T0)
    for minutes in (1, 120, 5 * 60 + 59):
        clock.set(T0 + timedelta(minutes=minutes))
        carrier.swap(MOM)
        await process_line(world.svc, mom, "event", clock.at)
    assert len(sender.sent) == 1
    assert row_summary(audit_rows(world.store, mom)) == [SENT, LIMITED, LIMITED]
    assert world.svc.metrics["rate_limited"] == 2

    clock.set(T0 + timedelta(minutes=1) + timedelta(hours=6))
    carrier.swap(MOM)
    await process_line(world.svc, mom, "event", clock.at)
    assert len(sender.sent) == 2
    assert row_summary(audit_rows(world.store, mom))[-1] == SENT


async def test_rate_limit_is_per_reason(world: World, mom: str, carrier, clock, sender) -> None:
    await process_line(world.svc, mom, "poll", T0)
    clock.set(T0 + timedelta(minutes=1))
    carrier.swap(MOM)
    await process_line(world.svc, mom, "event", clock.at)
    clock.set(T0 + timedelta(minutes=2))
    carrier.lines[MOM].call_forwarding = "unconditional"
    await process_line(world.svc, mom, "poll", clock.at)
    assert [r[2] for r in row_summary(audit_rows(world.store, mom))] == [
        ("SIM_SWAPPED_RECENT",),
        ("CALL_FORWARDING_SET",),
    ]
    assert len(sender.sent) == 2
