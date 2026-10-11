"""D8/D9 (code-vs-docs.md): Alerts sends down the **line-holder's** chain (06 §11.2–11.3).

Acceptance criteria 06 §11.5 14–22, on the settable `FixedClock` (no wall clock). The shape under test is the
product path: the Watch belongs to the line-holder (`watcher_user_id = user-asish`), profile `transplant`,
`escalation = [partner (ack), neighbour]`, and both contacts hold an active `watch` grant on Asish's line under the
alias "asish" (06 §11.4). The older shape — a partner-owned Watch — is covered by test_escalation.py.

New rules exercised here: per-step grant re-check with `SUPPRESSED_REVOKED` and no wait (11.2.1), the per-recipient
alias (11.2.2), the parked snapshot `remaining` (11.3), and the ignored-reply note going to the next chain step
when the watcher is the line-holder (11.3).

Timeline used throughout: baseline poll at T0, the line goes dark at T0+1 min (event), polls at T0+20 (19 min dark)
and T0+21 (20 min dark).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from alerts import escalation
from alerts import state as S
from alerts.runner import process_line
from alerts.state import ALERTS_STATE
from alerts.templates import ACK_IGNORED_NOTE
from alerts.testing import ASISH, PARTNER, T0, FakeLine, World, audit_rows, row_summary
from tower_consent import EscalationStep, Watch, get_watch, revoke, upsert_watch

from tests.helpers.patterns import HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.integration

M = timedelta(minutes=1)
OWNER = "user-asish"
P = "user-partner"
N = "user-neighbour"
ALIAS_START = (
    "asish's phone"  # 06 §11.2.2: each contact's SMS names the line by that contact's own grant alias
)
CHAIN = [(P, True), (N, False)]


def owner_transplant(world: World, carrier: Any, *, sim_change_at: datetime | None = None) -> str:
    world.standard()
    world.grant_watch(ASISH, OWNER, P, "asish")
    world.grant_watch(ASISH, OWNER, N, "asish")
    world.watch(ASISH, OWNER, "transplant", CHAIN)
    carrier.lines[ASISH] = FakeLine(
        sim_change_at=sim_change_at or T0 - timedelta(days=30), last_status_time=T0
    )
    return world.lines[ASISH]


async def baseline_then_dark(
    world: World, carrier: Any, clock: Any, line: str, *, at: datetime = T0 + M
) -> None:
    await process_line(world.svc, line, "poll", T0)
    clock.set(at)
    carrier.set_reachable(ASISH, False)
    await process_line(world.svc, line, "event", clock.at)


async def poll_at(world: World, clock: Any, line: str, at: datetime) -> None:
    clock.set(at)
    await process_line(world.svc, line, "poll", clock.at)


def chain_sent(sender: Any) -> list[Any]:
    return [s for s in sender.sent if s.label.startswith("chain:")]


def labels(sender: Any) -> list[str]:
    return [s.label for s in chain_sent(sender)]


async def first_alert(world: World, carrier: Any, clock: Any, sender: Any, **kw: Any) -> str:
    """Up to the partner's text at +21; the sender is cleared afterwards."""
    line = owner_transplant(world, carrier, **kw)
    await baseline_then_dark(world, carrier, clock, line)
    await poll_at(world, clock, line, T0 + 21 * M)
    assert labels(sender) == [f"chain:{P}"], labels(sender)
    sender.sent.clear()
    return line


# --- criterion 14 ------------------------------------------------------------------------------------------


async def test_c14_twenty_minutes_dark_texts_the_partner_by_alias(
    world: World, carrier, clock, sender
) -> None:
    line = owner_transplant(world, carrier)
    await baseline_then_dark(world, carrier, clock, line)

    await poll_at(world, clock, line, T0 + 20 * M)  # 19 min dark
    assert sender.sent == []

    audit_at_send: dict[str, list[Any]] = {}
    orig = sender.send

    async def spy(e164: str, body: str, *, label: str) -> None:
        audit_at_send[label] = row_summary(audit_rows(world.store, line))
        await orig(e164, body, label=label)

    sender.send = spy
    await poll_at(world, clock, line, T0 + 21 * M)  # 20 min dark

    chain = chain_sent(sender)
    assert [s.label for s in chain] == [f"chain:{P}"]  # the neighbour is not texted
    assert chain[0].body.startswith(ALIAS_START), chain[0].body
    assert chain[0].to_e164 == PARTNER
    assert any(o == "changed" and "UNREACHABLE" in c for _, o, c, _ in audit_at_send[f"chain:{P}"]), (
        "the changed · UNREACHABLE row must exist before the SMS"
    )
    holder = [s for s in sender.sent if s.label == f"line_holder:{OWNER}"]
    assert len(holder) == 1 and not holder[0].body.startswith("asish")  # the owner's copy has no alias
    assert S.get_escalation(world.store, line, OWNER) is not None  # parked on the partner (requires_ack)


# --- criterion 15 ------------------------------------------------------------------------------------------


async def test_c15_one_reachable_resets_the_twenty_minutes(world: World, carrier, clock, sender) -> None:
    line = owner_transplant(world, carrier)
    await baseline_then_dark(world, carrier, clock, line)  # dark from +1
    clock.set(T0 + 11 * M)
    carrier.set_reachable(ASISH, True)  # one `true` after 10 min dark
    await process_line(world.svc, line, "event", clock.at)
    clock.set(T0 + 12 * M)
    carrier.set_reachable(ASISH, False)  # dark again from +12
    await process_line(world.svc, line, "event", clock.at)

    await poll_at(world, clock, line, T0 + 21 * M)  # 20 min since the *first* dark: not continuous
    await poll_at(world, clock, line, T0 + 31 * M)  # 19 min since the new start
    assert sender.sent == []

    await poll_at(world, clock, line, T0 + 32 * M)  # 20 min since the new start
    chain = chain_sent(sender)
    assert [s.label for s in chain] == [f"chain:{P}"]
    assert chain[0].body.startswith(ALIAS_START)


# --- criterion 16 ------------------------------------------------------------------------------------------


async def test_c16_no_ack_in_15_minutes_texts_the_neighbour(world: World, carrier, clock, sender) -> None:
    line = await first_alert(world, carrier, clock, sender)
    clock.set(T0 + 35 * M)
    assert await escalation.tick(world.svc, clock.at) == []
    clock.set(T0 + 36 * M)
    assert await escalation.tick(world.svc, clock.at) == [f"chain:{N}"]
    (sms,) = chain_sent(sender)
    assert sms.body.startswith(ALIAS_START), sms.body  # the neighbour's own grant alias
    last = row_summary(audit_rows(world.store, line))[-1]
    assert last[1:3] == ("changed", ("UNREACHABLE",))
    assert S.get_escalation(world.store, line, OWNER) is None  # last step: no ack required, chain over


async def test_c16_ok_from_the_partner_stops_the_chain(world: World, carrier, clock, sender) -> None:
    line = await first_alert(world, carrier, clock, sender)
    clock.set(T0 + 25 * M)
    assert await escalation.handle_reply(world.svc, PARTNER, "OK", clock.at) == ["accepted"]
    assert row_summary(audit_rows(world.store, line))[-1] == ("event", "ok", ("OK",), None)
    for minutes in (36, 50, 90):
        clock.set(T0 + minutes * M)
        assert await escalation.tick(world.svc, clock.at) == []
    assert sender.sent == []  # the neighbour is never texted


# --- criterion 17 ------------------------------------------------------------------------------------------


async def test_c17_ok_from_the_swapped_watched_line_is_ignored_note_goes_to_next_step(
    world: World, carrier, clock, sender
) -> None:
    line = owner_transplant(world, carrier, sim_change_at=T0 - timedelta(hours=1))  # swapped < 24 h ago
    await baseline_then_dark(world, carrier, clock, line)
    await poll_at(world, clock, line, T0 + 21 * M)
    assert labels(sender) == [f"chain:{P}"]

    clock.set(T0 + 23 * M)
    assert await escalation.handle_reply(world.svc, ASISH, "OK", clock.at) == ["ignored"]
    assert row_summary(audit_rows(world.store, line))[-1] == (
        "event",
        "suppressed",
        ("ACK_IGNORED_SWAPPED_LINE",),
        None,
    )

    clock.set(T0 + 36 * M)
    texted = await escalation.tick(world.svc, clock.at)
    assert texted == [f"chain:{N}"], (
        texted
    )  # the note goes to the next step, not to the watcher (the swapped line)
    neighbour = [s for s in sender.sent if s.label == f"chain:{N}"]
    assert len(neighbour) == 1 and neighbour[0].body.endswith(ACK_IGNORED_NOTE)
    assert ASISH not in [s.to_e164 for s in sender.sent]  # never text the swapped line


# --- criterion 18 ------------------------------------------------------------------------------------------


async def test_c18_partner_revoked_after_first_sms_neighbour_still_texted(
    world: World, carrier, clock, sender
) -> None:
    line = await first_alert(world, carrier, clock, sender)
    revoke(world.store, line, P, "watch", revoked_by=OWNER, now=T0 + 22 * M)
    clock.set(T0 + 36 * M)
    assert await escalation.tick(world.svc, clock.at) == [f"chain:{N}"]  # from the snapshot


async def test_c18_neighbour_revoked_tick_suppresses_and_clears(world: World, carrier, clock, sender) -> None:
    line = await first_alert(world, carrier, clock, sender)
    n_rows = len(audit_rows(world.store, line))
    revoke(world.store, line, N, "watch", revoked_by=OWNER, now=T0 + 22 * M)
    clock.set(T0 + 36 * M)
    assert await escalation.tick(world.svc, clock.at) == []
    assert sender.sent == []
    new = row_summary(audit_rows(world.store, line))[n_rows:]
    assert any(o == "suppressed" and c == ("SUPPRESSED_REVOKED",) for _, o, c, _ in new), new
    assert S.get_escalation(world.store, line, OWNER) is None


# --- criterion 19 ------------------------------------------------------------------------------------------


async def test_c19_revoked_contact_still_in_chain_is_skipped_without_waiting(
    world: World, carrier, clock, sender
) -> None:
    line = owner_transplant(world, carrier)
    revoke(world.store, line, P, "watch", revoked_by=OWNER, now=T0 - M)
    # the second write of 04 §9.4 failed: the partner is still in the stored chain
    upsert_watch(
        world.store,
        Watch(
            line_id=line,
            watcher_user_id=OWNER,
            profile="transplant",
            enabled=True,
            escalation=[EscalationStep(user_id=u, requires_ack=a) for u, a in CHAIN],
        ),
    )
    owner = get_watch(world.store, line, OWNER)
    assert owner is not None and [s.user_id for s in owner.escalation] == [P, N]

    await baseline_then_dark(world, carrier, clock, line)
    n_rows = len(audit_rows(world.store, line))
    await poll_at(world, clock, line, T0 + 21 * M)

    assert labels(sender) == [f"chain:{N}"]  # at once, no 15-minute wait
    assert PARTNER not in [s.to_e164 for s in sender.sent]
    assert chain_sent(sender)[0].body.startswith(ALIAS_START)
    new = row_summary(audit_rows(world.store, line))[n_rows:]
    assert any(o == "suppressed" and c == ("SUPPRESSED_REVOKED",) for _, o, c, _ in new), new
    assert any(o == "changed" and c == ("UNREACHABLE",) for _, o, c, _ in new), new


# --- criterion 20 ------------------------------------------------------------------------------------------


async def test_c20_second_dark_spell_within_6h_is_suppressed(world: World, carrier, clock, sender) -> None:
    line = await first_alert(world, carrier, clock, sender)
    clock.set(T0 + 22 * M)
    assert await escalation.handle_reply(world.svc, PARTNER, "OK", clock.at) == ["accepted"]
    clock.set(T0 + 30 * M)
    carrier.set_reachable(ASISH, True)
    await process_line(world.svc, line, "event", clock.at)
    clock.set(T0 + 31 * M)
    carrier.set_reachable(ASISH, False)
    await process_line(world.svc, line, "event", clock.at)
    n_rows = len(audit_rows(world.store, line))

    await poll_at(world, clock, line, T0 + 51 * M)  # 20 min dark again, 30 min after the first text
    assert sender.sent == []
    new = row_summary(audit_rows(world.store, line))[n_rows:]
    assert [(o, c) for _, o, c, _ in new] == [("suppressed", ("UNREACHABLE",))], new
    assert S.get_escalation(world.store, line, OWNER) is None  # no parked chain


# --- criterion 21 ------------------------------------------------------------------------------------------


async def test_c21_reorder_while_parked_follows_the_snapshot(world: World, carrier, clock, sender) -> None:
    line = await first_alert(world, carrier, clock, sender)
    upsert_watch(
        world.store,
        Watch(
            line_id=line,
            watcher_user_id=OWNER,
            profile="transplant",
            enabled=True,
            escalation=[EscalationStep(user_id=N, requires_ack=True), EscalationStep(user_id=P)],
        ),
    )
    clock.set(T0 + 36 * M)
    assert await escalation.tick(world.svc, clock.at) == [f"chain:{N}"]


async def test_parked_escalation_carries_the_remaining_steps(world: World, carrier, clock, sender) -> None:
    """06 §11.3: the parked row gains `remaining: [{user_id, requires_ack}]`, the steps after the one texted."""
    line = await first_alert(world, carrier, clock, sender)
    item = world.store.get(ALERTS_STATE, {"pk": f"esc#{line}#{OWNER}"})
    assert item is not None
    assert item.get("remaining") == [{"user_id": N, "requires_ack": False}]


# --- criterion 22 ------------------------------------------------------------------------------------------


async def test_c22_no_sms_body_has_a_number_or_health_word(world: World, carrier, clock, sender) -> None:
    line = owner_transplant(world, carrier, sim_change_at=T0 - timedelta(hours=1))
    await baseline_then_dark(world, carrier, clock, line)
    await poll_at(world, clock, line, T0 + 21 * M)
    clock.set(T0 + 23 * M)
    await escalation.handle_reply(world.svc, ASISH, "OK", clock.at)
    clock.set(T0 + 36 * M)
    await escalation.tick(world.svc, clock.at)
    sent = list(sender.sent)
    assert {s.label for s in sent} >= {f"chain:{P}", f"chain:{N}"}
    for s in sent:
        assert not phone_hits(s.body), s.label
        assert not HEALTH_WORDS.search(s.body), s.label
        assert not phone_hits(s.label)
