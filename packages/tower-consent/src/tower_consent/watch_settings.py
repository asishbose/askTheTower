"""The line-holder's watch settings: profile and ordered contacts (04 §9, resolves D8/D9).

Stored on the line-holder's own Watch row (`Watches{line_id, watcher_user_id = owner}`): `profile` and
`escalation[]`. No new table, no new attribute. A contact is a `user_id` holding an active `watch` grant on the
line — that grant is the contact's consent to be texted (04 §9.1). `enabled` stays voice's: a first save creates
the row off, a later save keeps it. Every refusal writes nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final, get_args

from tower_consent import tables as T
from tower_consent.errors import InvalidContacts, InvalidProfile, NoContact
from tower_consent.grants import list_grants, owned_line
from tower_consent.models import Profile, Watch
from tower_consent.store import Store
from tower_consent.watches import chain_of

MAX_CONTACTS: Final = 3
PROFILES: Final[frozenset[str]] = frozenset(get_args(Profile))
NEEDS_CONTACT: Final[frozenset[str]] = frozenset({"transplant", "care"})


def set_watch_settings(
    store: Store,
    line_id: str,
    acting_user_id: str,
    profile: str,
    contacts: list[str],
    now: datetime,
) -> Watch:
    """Validate and save the line-holder's profile and contacts; return the stored Watch.

    Raises `LineNotFound` / `NotLineOwner` (not the line-holder), `InvalidProfile`, `InvalidContacts` (no active
    `watch` grant, the owner, a duplicate, more than three) or `NoContact` (`transplant`/`care` with none). The
    write is one UpdateItem; `last_state` and `subscription_ids` are never touched. `now` is accepted for the
    04 §9.2 contract (callers audit with it); the row itself carries no timestamp.
    """
    line = owned_line(store, line_id, acting_user_id)
    if profile not in PROFILES:
        raise InvalidProfile("profile must be self, transplant or care")
    _check_contacts(store, line_id, line.owner_user_id, contacts)
    if profile in NEEDS_CONTACT and not contacts:
        raise NoContact("pick at least one person to text")
    item = store.update(
        T.WATCHES,
        {"line_id": line_id, "watcher_user_id": line.owner_user_id},
        "SET #p = :p, #esc = :esc, #e = if_not_exists(#e, :off), #s = if_not_exists(#s, :none)",
        names={"#p": "profile", "#esc": "escalation", "#e": "enabled", "#s": "subscription_ids"},
        values={
            ":p": profile,
            ":esc": [s.to_item() for s in chain_of(contacts)],
            ":off": False,
            ":none": [],
        },
    )
    return Watch.model_validate(item)


def _check_contacts(store: Store, line_id: str, owner_user_id: str, contacts: list[str]) -> None:
    if len(contacts) > MAX_CONTACTS or len(set(contacts)) != len(contacts) or owner_user_id in contacts:
        raise InvalidContacts("at most three different people, not the line-holder")
    if not contacts:
        return
    watchers = {
        g.grantee_user_id for g in list_grants(store, line_id, include_revoked=False) if g.grant == "watch"
    }
    if not set(contacts) <= watchers:
        raise InvalidContacts("every contact needs an active watch grant on this line")
