"""D8/D9 (code-vs-docs.md): `watch_line` keeps the line-holder's saved profile and contacts (06 §11.1, 02 §2).

Acceptance criteria 06 §11.5 9–13. The saved settings are written straight to the owner's Watch row here (what
`tower_consent.set_watch_settings` would leave: 04 §9.1), so these tests exercise Tower only.

- `facts` gains `profile` (`self | transplant | care | null`): the stored value, also when off; null with no Watch.
- `enabled` with a `transplant`/`care` profile uses a second fixed summary (no interpolation, no digits).
- Contacts never appear in a ToolResult: no user id, no alias, no number.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from tower_consent import EscalationStep, Watch, get_watch, grant, tables, upsert_watch

from tests.privacy.patterns import phone_hits

pytestmark = pytest.mark.integration

OWNER = "user-asish"
PARTNER = "user-partner"
NEIGHBOUR = "user-neighbour"
# Aliases the line-holder gave the contacts' grants; chosen to be distinctive so a leak is visible.
PARTNER_ALIAS = "darling"
NEIGHBOUR_ALIAS = "next door"

SUMMARY_SELF = "Alerts are on for that line. You'll get a text if it's SIM-swapped or forwarded."
SUMMARY_REACH = "Alerts are on for that line. A text goes out if it's SIM-swapped, forwarded, or off the network too long."

CHAIN = [
    EscalationStep(user_id=PARTNER, requires_ack=True),
    EscalationStep(user_id=NEIGHBOUR, requires_ack=False),
]


def facts(result: Any) -> dict[str, Any]:
    wire: dict[str, Any] = result.to_wire()
    return dict(wire["facts"])


def saved_transplant(stack: Any, *, enabled: bool = False) -> str:
    """Asish's line with transplant settings saved on the page: two contacts with active `watch` grants."""
    line: str = stack.seed.asish_line
    now = stack.clock.at
    grant(stack.store, line, PARTNER, "watch", PARTNER_ALIAS, granted_by=OWNER, now=now)
    grant(stack.store, line, NEIGHBOUR, "watch", NEIGHBOUR_ALIAS, granted_by=OWNER, now=now)
    upsert_watch(
        stack.store,
        Watch(line_id=line, watcher_user_id=OWNER, profile="transplant", enabled=enabled, escalation=CHAIN),
    )
    return line


def assert_no_contacts(result: Any) -> None:
    text = json.dumps(result.to_wire(), sort_keys=True)
    for leak in (PARTNER, NEIGHBOUR, PARTNER_ALIAS, NEIGHBOUR_ALIAS):
        assert leak not in text, f"contact {leak!r} in a ToolResult"
    assert not phone_hits(text)


# --- criterion 9 -------------------------------------------------------------------------------------------


async def test_c9_enable_keeps_saved_transplant_settings(stack: Any) -> None:
    line = saved_transplant(stack)
    r = await stack.watch_line(OWNER, "self", True)
    assert r.reason_codes == ["OK"]

    w = get_watch(stack.store, line, OWNER)
    assert w is not None and w.enabled is True
    assert w.profile == "transplant" and w.escalation == CHAIN

    assert stack.alerts.calls[-1] == {
        "line_id": line,
        "watcher_user_id": OWNER,
        "enable": True,
        "profile": "transplant",
    }
    assert facts(r).get("profile") == "transplant"
    assert r.summary == SUMMARY_REACH
    assert not any(ch.isdigit() for ch in r.summary)
    assert_no_contacts(r)


async def test_c9_care_profile_uses_the_reachability_summary(stack: Any) -> None:
    line = stack.seed.asish_line
    grant(stack.store, line, NEIGHBOUR, "watch", NEIGHBOUR_ALIAS, granted_by=OWNER, now=stack.clock.at)
    upsert_watch(
        stack.store,
        Watch(
            line_id=line,
            watcher_user_id=OWNER,
            profile="care",
            enabled=False,
            escalation=[EscalationStep(user_id=NEIGHBOUR)],
        ),
    )
    r = await stack.watch_line(OWNER, "self", True)
    assert facts(r).get("profile") == "care"
    assert r.summary == SUMMARY_REACH
    assert_no_contacts(r)


# --- criterion 10 ------------------------------------------------------------------------------------------


async def test_c10_no_watch_defaults_to_self(stack: Any) -> None:
    line = stack.seed.asish_line
    assert get_watch(stack.store, line, OWNER) is None
    r = await stack.watch_line(OWNER, "self", True)
    w = get_watch(stack.store, line, OWNER)
    assert w is not None and w.profile == "self" and w.enabled is True
    assert w.escalation == [EscalationStep(user_id=OWNER, requires_ack=False)]
    assert facts(r).get("profile") == "self"
    assert r.summary == SUMMARY_SELF  # the self summary is unchanged


# --- criterion 11 ------------------------------------------------------------------------------------------


async def test_c11_off_then_on_gives_back_the_same_profile_and_chain(stack: Any) -> None:
    line = saved_transplant(stack, enabled=True)
    r = await stack.watch_line(OWNER, "self", False)
    assert r.reason_codes == ["OK"] and facts(r)["watching"] is False
    assert facts(r).get("profile") == "transplant"  # kept for next time, reported while off
    off = get_watch(stack.store, line, OWNER)
    assert (
        off is not None and off.enabled is False and off.profile == "transplant" and off.escalation == CHAIN
    )

    r = await stack.watch_line(OWNER, "self", True)
    on = get_watch(stack.store, line, OWNER)
    assert on is not None and on.enabled is True and on.profile == "transplant" and on.escalation == CHAIN
    assert facts(r).get("profile") == "transplant"
    assert [c["profile"] for c in stack.alerts.calls[-2:]] == ["transplant", "transplant"]


# --- criterion 12 ------------------------------------------------------------------------------------------


async def test_c12_grantee_watch_never_reads_or_copies_the_line_holders_settings(stack: Any) -> None:
    mom_line = stack.seed.mom_line
    mom = stack.seed.mom_user
    # Mom saved transplant settings on her own line (Asish holds a `watch` grant "mom" from the seed)
    upsert_watch(
        stack.store,
        Watch(
            line_id=mom_line,
            watcher_user_id=mom,
            profile="transplant",
            enabled=True,
            escalation=[EscalationStep(user_id=OWNER, requires_ack=True), EscalationStep(user_id=NEIGHBOUR)],
        ),
    )
    key = {"line_id": mom_line, "watcher_user_id": mom}
    before = stack.store.get(tables.WATCHES, key)

    reads: list[dict[str, Any]] = []

    def on_get(params: dict[str, Any], **_: Any) -> None:
        reads.append(params.get("Key", {}))

    stack.inject("GetItem", on_get)
    r = await stack.watch_line(OWNER, "mom", True)
    assert r.reason_codes == ["OK"]

    mine = get_watch(stack.store, mom_line, OWNER)
    assert mine is not None and mine.profile == "care" and mine.enabled is True
    assert mine.escalation == [EscalationStep(user_id=OWNER, requires_ack=False)]
    assert stack.store.get(tables.WATCHES, key) == before  # Mom's row unchanged
    mom_reads = [k for k in reads if k.get("watcher_user_id", {}).get("S") == mom and "line_id" in k]
    assert mom_reads == [], "Mom's Watch row was read for settings"
    assert facts(r).get("profile") == "care"
    assert stack.alerts.calls[-1]["profile"] == "care"


# --- criterion 13 ------------------------------------------------------------------------------------------


async def test_c13_status_reports_the_stored_profile_while_off(stack: Any) -> None:
    saved_transplant(stack, enabled=False)
    r = await stack.watch_line(OWNER, "self", None)
    assert r.reason_codes == ["OK"]
    f = facts(r)
    assert f["watching"] is False
    assert f.get("profile") == "transplant"
    # The owner's status already lists grants as {alias, grant} ("who can see my line?", 02 §2); that is not the
    # chain. What must not appear is a contact's user id or a number.
    text = json.dumps(r.to_wire(), sort_keys=True)
    assert PARTNER not in text and NEIGHBOUR not in text and not phone_hits(text)


async def test_c13_status_with_no_watch_reports_null_profile(stack: Any) -> None:
    r = await stack.watch_line(OWNER, "self", None)
    f = facts(r)
    assert "profile" in f, "facts.profile is part of the contract (02 §2), null with no Watch"
    assert f["profile"] is None
