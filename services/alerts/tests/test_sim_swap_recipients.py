"""06 §3 / e2e §8.4: after a SIM swap the swapped line is never texted; the backup phone is used if any;
the watcher always."""

from __future__ import annotations

from datetime import timedelta

import pytest
from alerts.runner import process_line
from alerts.testing import ASISH, MOM, MOM_BACKUP, NEIGHBOUR, T0, FakeLine, World

pytestmark = pytest.mark.integration


def _sent_to(sender) -> list[str]:
    return [s.to_e164 for s in sender.sent]


async def _swap(world: World, line_id: str, carrier, clock) -> None:
    await process_line(world.svc, line_id, "poll", T0)
    clock.set(T0 + timedelta(minutes=1))
    carrier.swap(MOM)
    await process_line(world.svc, line_id, "event", clock.at)


async def test_swapped_line_excluded_watcher_told(world: World, mom: str, carrier, clock, sender) -> None:
    await _swap(world, mom, carrier, clock)
    assert [s.label for s in sender.sent] == ["chain:user-asish"]
    assert MOM not in _sent_to(sender)
    assert _sent_to(sender) == [ASISH]
    assert (
        sender.sent[0].body == "SIM moved to another device at 10:01 today. Not you? Call your carrier now."
    )


async def test_backup_phone_used(world: World, carrier, clock, sender) -> None:
    world.standard(mom_backup=True)
    world.watch(MOM, "user-asish", "care")
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60))
    carrier.lines[ASISH] = FakeLine()
    await _swap(world, world.lines[MOM], carrier, clock)
    assert [s.label for s in sender.sent] == ["backup:user-mom", "chain:user-asish"]
    assert _sent_to(sender) == [MOM_BACKUP, ASISH]


async def test_self_watch_on_swapped_line(world: World, carrier, clock, sender) -> None:
    """Mom watches her own line: her alert phone *is* the swapped line → skipped (no backup) → the next
    person in her chain is texted instead. A contact on the line-holder's chain holds an active `watch` grant
    (04 §9.1); one without it is skipped (06 §11.2.1)."""
    world.standard()
    world.grant_watch(MOM, "user-mom", "user-neighbour", "mom")
    world.watch(MOM, "user-mom", "self", [("user-mom", False), ("user-neighbour", False)])
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60))
    await _swap(world, world.lines[MOM], carrier, clock)
    assert MOM not in _sent_to(sender)
    assert [s.label for s in sender.sent] == ["chain:user-neighbour"]
    assert _sent_to(sender) == [NEIGHBOUR]


async def test_self_watch_with_backup(world: World, carrier, clock, sender) -> None:
    world.standard(mom_backup=True)
    world.watch(MOM, "user-mom", "self")
    carrier.lines[MOM] = FakeLine(sim_change_at=T0 - timedelta(days=60))
    await _swap(world, world.lines[MOM], carrier, clock)
    assert [s.label for s in sender.sent] == ["backup:user-mom"]
    assert _sent_to(sender) == [MOM_BACKUP]


async def test_line_not_texted_on_later_alert_within_24h(
    world: World, mom: str, carrier, clock, sender
) -> None:
    """A reachability alert 4 h after the swap: the line-holder is still not texted at the swapped number."""
    await _swap(world, mom, carrier, clock)
    sender.sent.clear()
    carrier.set_reachable(MOM, False)
    await process_line(world.svc, mom, "poll", clock.at)
    clock.set(clock.at + timedelta(hours=4))
    await process_line(world.svc, mom, "poll", clock.at)
    assert [s.label for s in sender.sent] == ["chain:user-asish"]
    assert MOM not in _sent_to(sender)
    assert sender.sent[0].body.startswith("mom's phone has been off the network since")


async def test_line_holder_texted_when_no_swap(world: World, mom: str, carrier, clock, sender) -> None:
    await process_line(world.svc, mom, "poll", T0)
    carrier.set_reachable(MOM, False)
    clock.set(T0 + timedelta(hours=4))
    await process_line(world.svc, mom, "poll", clock.at)
    assert [s.label for s in sender.sent] == ["line_holder:user-mom", "chain:user-asish"]
    assert _sent_to(sender) == [MOM, ASISH]
