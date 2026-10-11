"""Users (04 §4): the Alexa-linked user and their encrypted alert phone."""

from __future__ import annotations

from datetime import datetime

from tower_consent import tables as T
from tower_consent.crypto import MsisdnCipher
from tower_consent.errors import ConditionFailed
from tower_consent.models import User
from tower_consent.store import Store


def ensure_user(store: Store, user_id: str, *, now: datetime, alexa_link_id: str | None = None) -> User:
    """Create the user if absent; return the stored row either way."""
    u = User(user_id=user_id, alexa_link_id=alexa_link_id, created_at=now)
    try:
        store.put(T.USERS, u.to_item(), condition="attribute_not_exists(user_id)")
        return u
    except ConditionFailed:
        item = store.get(T.USERS, {"user_id": user_id})
        assert item is not None  # noqa: S101 - just failed a not-exists condition
        return User.model_validate(item)


def get_user(store: Store, user_id: str) -> User | None:
    item = store.get(T.USERS, {"user_id": user_id})
    return User.model_validate(item) if item else None


def set_alert_phone(store: Store, cipher: MsisdnCipher, user_id: str, e164: str) -> User:
    """Store the alert phone encrypted (06 §3: defaults to the bound line; set explicitly otherwise)."""
    item = store.update(
        T.USERS,
        {"user_id": user_id},
        "SET alert_phone_enc = :p",
        condition="attribute_exists(user_id)",
        values={":p": cipher.encrypt(e164)},
    )
    return User.model_validate(item)
