"""06 §3: escalation after ESCALATE_NEXT without an ack; acks; ACK_DISTRUST for a swapped line's replies."""

from __future__ import annotations

from datetime import timedelta

import pytest
from alerts import escalation
from alerts.runner import process_line
from alerts.templates import ACK_IGNORED_NOTE
from alerts.testing import ASISH, MOM, PARTNER, T0, FakeLine, World, audit_rows, row_summary

pytestmark = pytest.mark.integration
M = timedelta(minutes=1)


async def _transplant_dark(world: World, carrier, clock, sender) -> str:
    """Asish's line watched by the partner: chain [partner (ack), neighbour]. Dark at +1, first text at +21."""
    world.standard()
    world.grant_watch(ASISH, "user-asish", "user-partner", "asish")
    world.watch(ASISH, "user-partner", "transplant", [("user-partner", True), ("user-neighbour", False)])
    carrier.lines[ASISH] = FakeLine(sim_change_at=T0 - timedelta(days=30), last_status_time=T0)
    line = world.lines[ASISH]
    await process_line(world.svc, line, "poll", T0)
    clock.set(T0 + M)
    carrier.set_reachable(ASISH, False)
    await process_line(world.svc, line, "event", clock.at)
    clock.set(T0 + 21 * M)
    await process_line(world.svc, line, "poll", clock.at)
    assert [s.label for s in sender.sent] == ["line_holder:user-asish", "chain:user-partner"]
    sender.sent.clear()
    return line


async def test_no_ack_in_15_min_texts_second_contact(world: World, carrier, clock, sender) -> None:
    line = await _transplant_dark(world, carrier, clock, sender)
    clock.set(T0 + 35 * M)  # 14 min later
    assert await escalation.tick(world.svc, clock.at) == []
    clock.set(T0 + 36 * M)  # 15 min
    assert await escalation.tick(world.svc, clock.at) == ["chain:user-neighbour"]
    clock.set(T0 + 60 * M)
    assert await escalation.tick(world.svc, clock.at) == []  # chain ended (no ack required of the last step)
    assert row_summary(audit_rows(world.store, line))[-1] == (
        "poll",
        "changed",
        ("UNREACHABLE",),
        "UNREACHABLE.sms",
    )


async def test_ack_stops_escalation(world: World, carrier, clock, sender) -> None:
    line = await _transplant_dark(world, carrier, clock, sender)
    clock.set(T0 + 25 * M)
    assert await escalation.handle_reply(world.svc, PARTNER, " ok ", clock.at) == ["accepted"]
    clock.set(T0 + 40 * M)
    assert await escalation.tick(world.svc, clock.at) == []
    assert sender.sent == []
    assert row_summary(audit_rows(world.store, line))[-1] == ("event", "ok", ("OK",), None)


async def test_non_ack_text_and_unknown_number_change_nothing(world: World, carrier, clock, sender) -> None:
    await _transplant_dark(world, carrier, clock, sender)
    assert await escalation.handle_reply(world.svc, PARTNER, "who is this", clock.at) == []
    assert await escalation.handle_reply(world.svc, "+16135550199", "OK", clock.at) == []
    clock.set(T0 + 36 * M)
    assert await escalation.tick(world.svc, clock.at) == ["chain:user-neighbour"]


async def _mom_swap_with_chain(world: World, carrier, clock, sender) -> str:
    world.standard()
    world.watch(MOM, "user-asish", "care", [("user-asish", True), ("user-neighbour", False)])
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60))
    carrier.lines[ASISH] = FakeLine(sim_change_at=T0 - timedelta(days=30))
    line = world.lines[MOM]
    await process_line(world.svc, line, "poll", T0)
    clock.set(T0 + M)
    carrier.swap(MOM)
    await process_line(world.svc, line, "event", clock.at)
    assert [s.label for s in sender.sent] == ["chain:user-asish"]
    sender.sent.clear()
    return line


async def test_ack_from_swapped_line_is_ignored_and_escalation_continues(
    world: World, carrier, clock, sender
) -> None:
    line = await _mom_swap_with_chain(world, carrier, clock, sender)
    clock.set(T0 + 3 * M)
    # whoever holds Mom's number now replies "OK" — maybe the attacker
    assert await escalation.handle_reply(world.svc, MOM, "OK", clock.at) == ["ignored"]
    assert row_summary(audit_rows(world.store, line))[-1] == (
        "event",
        "suppressed",
        ("ACK_IGNORED_SWAPPED_LINE",),
        None,
    )
    clock.set(T0 + 16 * M)
    texted = await escalation.tick(world.svc, clock.at)
    assert texted == ["chain:user-asish", "chain:user-neighbour"]
    assert sender.sent[0].body.endswith(ACK_IGNORED_NOTE)  # the watcher's next message says so
    assert not sender.sent[1].body.endswith(ACK_IGNORED_NOTE)
    assert MOM not in [s.to_e164 for s in sender.sent]


async def test_same_ack_from_watcher_is_accepted(world: World, carrier, clock, sender) -> None:
    line = await _mom_swap_with_chain(world, carrier, clock, sender)
    clock.set(T0 + 3 * M)
    assert await escalation.handle_reply(world.svc, ASISH, "OK", clock.at) == ["accepted"]
    clock.set(T0 + 30 * M)
    assert await escalation.tick(world.svc, clock.at) == []
    assert sender.sent == []
    assert row_summary(audit_rows(world.store, line))[-1] == ("event", "ok", ("OK",), None)


async def test_ack_distrust_ends_after_24h(world: World, carrier, clock, sender) -> None:
    await _mom_swap_with_chain(world, carrier, clock, sender)
    clock.set(T0 + timedelta(hours=24, minutes=2))
    # the escalation is long over by now; a fresh one parks on Mom's own chain to test the window
    from alerts import state as S

    esc = S.get_escalation(world.store, world.lines[MOM], "user-asish")
    assert esc is not None  # still parked (nobody acked); neighbour not yet texted because no tick ran
    assert await escalation.handle_reply(world.svc, MOM, "OK", clock.at) == ["accepted"]


async def test_carrier_error_on_swap_check_fails_closed(world: World, carrier, clock, sender) -> None:
    await _mom_swap_with_chain(world, carrier, clock, sender)
    carrier.fail["sim_swap_check"] = -1
    # the watcher's own line has no Watch, so the carrier is asked — and it errors → not trusted
    assert await escalation.handle_reply(world.svc, ASISH, "OK", clock.at) == ["ignored"]
