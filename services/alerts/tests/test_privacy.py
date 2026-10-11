"""00 privacy invariants for Alerts: every SMS body — no E.164, no health word; logs and audit rows carry no
number. (The `sender` fixture also checks every body any test in this suite produces; the `store` fixture
sweeps every table, `AlertsState` included.)"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from alerts.runner import process_line
from alerts.templates import ACK_IGNORED_NOTE, render
from alerts.testing import ASISH, MOM, MOM_BACKUP, NEIGHBOUR, PARTNER, T0, FakeLine, World, audit_rows
from tower_policy import Facts, ReasonCode

from tests.privacy.patterns import HEALTH_WORDS, phone_hits

NUMBERS = (ASISH, MOM, MOM_BACKUP, NEIGHBOUR, PARTNER)
CHANGED = (ReasonCode.SIM_SWAPPED_RECENT, ReasonCode.CALL_FORWARDING_SET, ReasonCode.UNREACHABLE)


@pytest.mark.unit
@pytest.mark.parametrize("alias", [None, "mom", "o'neil", "grand-pa"])
@pytest.mark.parametrize("tz", ["America/Toronto", "Asia/Kolkata", "UTC"])
def test_every_template_combination_is_clean(alias: str | None, tz: str) -> None:
    facts = Facts(
        fetched_at=T0,
        sim_swapped=True,
        latest_sim_change=T0 - timedelta(days=2),
        call_forwarding="unconditional",
        reachable=False,
        last_status_time=T0 - timedelta(minutes=25),
    )
    import itertools

    for n in (1, 2, 3):
        for codes in itertools.combinations(CHANGED, n):
            body = render(codes, facts, alias=alias, tz=tz, now=T0)
            for text in (body, body + " " + ACK_IGNORED_NOTE):
                assert body and not phone_hits(text), text
                assert not HEALTH_WORDS.search(text), text
                assert len(text) <= 3 * 160


@pytest.mark.integration
async def test_logs_and_audit_carry_no_numbers(world: World, carrier, clock, sender, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    world.standard(mom_backup=True)
    world.watch(MOM, "user-asish", "care", [("user-asish", True), ("user-neighbour", False)])
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60))
    carrier.lines[ASISH] = FakeLine()
    line = world.lines[MOM]
    from alerts import escalation

    await process_line(world.svc, line, "poll", T0)
    clock.set(T0 + timedelta(minutes=1))
    carrier.swap(MOM)
    carrier.lines[MOM].call_forwarding = "unconditional"
    await process_line(world.svc, line, "event", clock.at)
    await escalation.handle_reply(world.svc, MOM, "OK", clock.at)
    carrier.set_reachable(MOM, False)
    clock.set(T0 + timedelta(hours=5))
    await process_line(world.svc, line, "poll", clock.at)
    await escalation.tick(world.svc, clock.at)
    sender.fail_next = 2
    clock.set(T0 + timedelta(hours=7))
    carrier.swap(MOM)
    await process_line(world.svc, line, "event", clock.at)

    assert len(sender.sent) >= 4
    assert {s.to_e164 for s in sender.sent} <= {ASISH, MOM_BACKUP, NEIGHBOUR}
    text = caplog.text  # everything, botocore DEBUG included: the raw numbers never appear
    for n in NUMBERS:
        assert n not in text and n.lstrip("+") not in text
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("alerts"))
    assert ours and not phone_hits(ours)  # our own log lines: nothing number-shaped at all
    for row in audit_rows(world.store, line):
        fields = {
            k: v for k, v in row.canonical_dict().items() if k != "ttl"
        }  # ttl: epoch seconds, by design
        assert not phone_hits(str(fields))
