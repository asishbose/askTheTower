"""D8/D9 (code-vs-docs.md): the line-holder's watch settings, `tower_consent.set_watch_settings` (04 §9).

Acceptance criteria 06 §11.5, library half of 1–4 and 6:

- the settings live on the owner's own Watch row (`Watches{line_id, watcher_user_id = owner}`), 04 §9.1;
- a contact is a `user_id` with an active `watch` grant on the line; `requires_ack` is derived (true on every step
  with a successor, false on the last); a new row starts `enabled=false`, later saves keep `enabled`;
- every refusal writes nothing (04 §9.2 table);
- `last_state` is never touched by a save (04 §9.4);
- revoking a contact's `watch` grant removes them from the owner's chain (04 §9.4), next to D7's `disable_watch`.

Specified, not yet built: every test here is expected to fail until `set_watch_settings` exists. The import is
inside each test so each one fails on its own rather than the module failing to collect.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from tower_consent import (
    EscalationStep,
    LastState,
    Store,
    Watch,
    bind_line,
    get_watch,
    grant,
    revoke,
    tables,
    upsert_watch,
)
from tower_consent.crypto import LineIdHasher, MsisdnCipher
from tower_consent.errors import ConsentError, LineNotFound, NotLineOwner

pytestmark = pytest.mark.integration

OWNER = "user-asish"
PARTNER = "user-partner"
NEIGHBOUR = "user-neighbour"
COUSIN = "user-cousin"  # reachability only: not a valid contact (04 §9.1)
EX = "user-ex"  # watch, revoked: not a valid contact
FRIEND = "user-friend"  # a third active watch grantee (for the "more than 3" case)
STRANGER = "user-stranger"  # no grant at all


def save(store: Store, line_id: str, actor: str, profile: str, contacts: list[str], now: datetime) -> Watch:
    from tower_consent import set_watch_settings  # specified in 04 §9.2; not yet built

    w: Watch = set_watch_settings(
        store, line_id=line_id, acting_user_id=actor, profile=profile, contacts=contacts, now=now
    )
    return w


def snapshot(store: Store) -> str:
    """Every row of every table, canonical: 'nothing written' means this string is unchanged."""
    dump = {
        t.name: sorted(store.scan_all(t), key=lambda i: json.dumps(i, sort_keys=True, default=str))
        for t in tables.TABLES
    }
    return json.dumps(dump, sort_keys=True, default=str)


def chain(w: Watch | None) -> list[tuple[str, bool]]:
    assert w is not None
    return [(s.user_id, s.requires_ack) for s in w.escalation]


@pytest.fixture
def lines(store: Store, hasher: LineIdHasher, cipher: MsisdnCipher, now: datetime) -> dict[str, str]:
    asish = bind_line(store, hasher, cipher, OWNER, "+16135550101", "auth_code", now=now).line_id
    mom = bind_line(store, hasher, cipher, "user-mom", "+16135550102", "auth_code", now=now).line_id
    for grantee in (PARTNER, NEIGHBOUR, FRIEND, EX):
        grant(store, asish, grantee, "watch", "asish", granted_by=OWNER, now=now)
    grant(store, asish, COUSIN, "reachability", "asish", granted_by=OWNER, now=now)
    revoke(store, asish, EX, "watch", revoked_by=OWNER, now=now + timedelta(seconds=1))
    return {"asish": asish, "mom": mom}


# --- criterion 1 -------------------------------------------------------------------------------------------


def test_c1_owner_saves_transplant_with_two_contacts(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    line = lines["asish"]
    w = save(store, line, OWNER, "transplant", [PARTNER, NEIGHBOUR], now)
    stored = get_watch(store, line, OWNER)  # the owner's own Watch row (04 §9.1)
    assert stored is not None
    assert stored.profile == "transplant"
    assert chain(stored) == [(PARTNER, True), (NEIGHBOUR, False)]
    assert stored.enabled is False  # a new row is created off; voice turns it on
    assert w == stored


def test_requires_ack_is_derived_for_three_contacts(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    line = lines["asish"]
    save(store, line, OWNER, "care", [NEIGHBOUR, FRIEND, PARTNER], now)
    assert chain(get_watch(store, line, OWNER)) == [(NEIGHBOUR, True), (FRIEND, True), (PARTNER, False)]


def test_later_save_keeps_enabled_as_voice_last_set_it(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    line = lines["asish"]
    save(store, line, OWNER, "transplant", [PARTNER], now)
    save(store, line, OWNER, "care", [NEIGHBOUR, PARTNER], now + timedelta(minutes=1))
    w = get_watch(store, line, OWNER)
    assert w is not None and w.enabled is False and w.profile == "care"
    assert chain(w) == [(NEIGHBOUR, True), (PARTNER, False)]


# --- criterion 2 -------------------------------------------------------------------------------------------


def test_c2_non_owner_is_refused_and_nothing_is_written(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    before = snapshot(store)
    with pytest.raises((NotLineOwner, LineNotFound)):
        save(store, lines["asish"], "user-mom", "transplant", [PARTNER], now)
    with pytest.raises((NotLineOwner, LineNotFound)):
        save(
            store, lines["asish"], PARTNER, "transplant", [NEIGHBOUR], now
        )  # a grantee is not the line-holder
    with pytest.raises((NotLineOwner, LineNotFound)):
        save(store, "ln_" + "z" * 64, OWNER, "transplant", [PARTNER], now)  # no such line
    assert snapshot(store) == before


@pytest.mark.parametrize(
    ("profile", "contacts", "why"),
    [
        ("transplant", [COUSIN], "reachability-only grant"),
        ("transplant", [EX], "revoked watch grant"),
        ("transplant", [STRANGER], "no grant"),
        ("transplant", [OWNER], "the owner as a contact"),
        ("transplant", [PARTNER, OWNER], "the owner as a later contact"),
        ("transplant", [PARTNER, PARTNER], "duplicate contact"),
        ("care", [PARTNER, NEIGHBOUR, FRIEND, PARTNER], "four contacts (also a duplicate)"),
        ("transplant", [], "transplant with no contact"),
        ("care", [], "care with no contact"),
        ("panic", [PARTNER], "profile not in the enum"),
        ("owner", [PARTNER], "profile not in the enum"),
    ],
)
def test_c2_refusals_write_nothing(
    store: Store, lines: dict[str, str], now: datetime, profile: str, contacts: list[str], why: str
) -> None:
    before = snapshot(store)
    with pytest.raises((ConsentError, ValueError)):
        save(store, lines["asish"], OWNER, profile, contacts, now)
    assert snapshot(store) == before, why


def test_c2_four_distinct_active_contacts_is_refused(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    line = lines["asish"]
    grant(store, line, "user-aunt", "watch", "asish", granted_by=OWNER, now=now)
    before = snapshot(store)
    with pytest.raises((ConsentError, ValueError)):
        save(store, line, OWNER, "care", [PARTNER, NEIGHBOUR, FRIEND, "user-aunt"], now)
    assert snapshot(store) == before


def test_c2_refusal_leaves_existing_settings_untouched(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    line = lines["asish"]
    save(store, line, OWNER, "transplant", [PARTNER, NEIGHBOUR], now)
    before = snapshot(store)
    with pytest.raises((ConsentError, ValueError)):
        save(store, line, OWNER, "transplant", [COUSIN], now + timedelta(minutes=1))
    assert snapshot(store) == before


# --- criterion 3 -------------------------------------------------------------------------------------------


def test_c3_self_with_no_contacts_is_accepted(store: Store, lines: dict[str, str], now: datetime) -> None:
    line = lines["asish"]
    save(store, line, OWNER, "self", [], now)
    w = get_watch(store, line, OWNER)
    assert w is not None and w.profile == "self" and w.escalation == []


def test_c3_self_with_no_contacts_clears_saved_settings(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    line = lines["asish"]
    save(store, line, OWNER, "transplant", [PARTNER, NEIGHBOUR], now)
    save(store, line, OWNER, "self", [], now + timedelta(minutes=1))
    w = get_watch(store, line, OWNER)
    assert w is not None and w.profile == "self" and w.escalation == []


# --- criterion 4 (library half) ----------------------------------------------------------------------------


def _raw_watch(store: Store, line: str) -> dict[str, Any]:
    item = store.get(tables.WATCHES, {"line_id": line, "watcher_user_id": OWNER})
    assert item is not None
    return item


def test_c4_save_on_enabled_watch_keeps_enabled_and_last_state(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    line = lines["asish"]
    dark = LastState(reachable=False, unreachable_since=now - timedelta(minutes=7), cf_status="none", at=now)
    upsert_watch(
        store,
        Watch(
            line_id=line,
            watcher_user_id=OWNER,
            profile="self",
            enabled=True,
            escalation=[EscalationStep(user_id=OWNER)],
            last_state=dark,
            subscription_ids=["sub-a"],
        ),
    )
    before = _raw_watch(store, line)["last_state"]
    save(store, line, OWNER, "transplant", [PARTNER, NEIGHBOUR], now + timedelta(minutes=1))
    after = _raw_watch(store, line)
    assert after["enabled"] is True
    assert json.dumps(after["last_state"], sort_keys=True) == json.dumps(before, sort_keys=True)
    w = get_watch(store, line, OWNER)
    assert w is not None and w.profile == "transplant" and chain(w) == [(PARTNER, True), (NEIGHBOUR, False)]


# --- criterion 6 (library half; also depends on D7 for the partner's own Watch) -----------------------------


def test_c6_revoking_a_contact_removes_them_from_the_owners_chain(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    line = lines["asish"]
    save(store, line, OWNER, "transplant", [PARTNER, NEIGHBOUR], now)
    upsert_watch(  # voice turned alerts on; the partner also watches the line themselves
        store,
        Watch(
            line_id=line,
            watcher_user_id=OWNER,
            profile="transplant",
            enabled=True,
            escalation=[
                EscalationStep(user_id=PARTNER, requires_ack=True),
                EscalationStep(user_id=NEIGHBOUR),
            ],
        ),
    )
    upsert_watch(store, Watch(line_id=line, watcher_user_id=PARTNER, profile="care", enabled=True))

    revoke(store, line, PARTNER, "watch", revoked_by=OWNER, now=now + timedelta(minutes=1))

    owner = get_watch(store, line, OWNER)
    assert chain(owner) == [(NEIGHBOUR, False)]
    assert (
        owner is not None and owner.enabled is True
    )  # revoking someone else never disables the owner's Watch
    assert owner.profile == "transplant"
    partner = get_watch(store, line, PARTNER)
    assert partner is not None and partner.enabled is False  # D7, unchanged


def test_c6_revoking_the_last_contact_rederives_requires_ack(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    """04 §9.1: `requires_ack` is derived — false on the last step — so the remaining head stops waiting."""
    line = lines["asish"]
    save(store, line, OWNER, "transplant", [PARTNER, NEIGHBOUR], now)
    revoke(store, line, NEIGHBOUR, "watch", revoked_by=OWNER, now=now + timedelta(minutes=1))
    assert chain(get_watch(store, line, OWNER)) == [(PARTNER, False)]


def test_revoking_reachability_does_not_touch_the_chain(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    line = lines["asish"]
    grant(store, line, PARTNER, "reachability", "ash", granted_by=OWNER, now=now)
    save(store, line, OWNER, "transplant", [PARTNER, NEIGHBOUR], now)
    revoke(store, line, PARTNER, "reachability", revoked_by=OWNER, now=now + timedelta(minutes=1))
    assert chain(get_watch(store, line, OWNER)) == [(PARTNER, True), (NEIGHBOUR, False)]
