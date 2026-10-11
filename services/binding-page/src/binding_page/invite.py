"""Invite codes: how a grantee tells the line-holder who they are (04 §3).

The grantee taps "Create invite code" on their own page; the code is short (8 letters, shown `ABCD-EFGH`), lives
24 h, maps to their `user_id`, and is stored in the `BindTokens` table with `kind="invite"`. The line-holder
types it into the grant form. It is consumed when a grant is made with it (single use), so a code passed around
can't be used to push grants (and alerts) at the grantee from strangers' lines.

Letters only, from an alphabet without look-alikes: a code can never look like a phone number.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from tower_consent import BindToken, Store, consume_bind_token, get_bind_token, tables
from tower_consent.errors import BindTokenRefused, ConditionFailed

INVITE_TTL = timedelta(hours=24)
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I, O
LENGTH = 8


def _new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def display(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def normalize(raw: str) -> str | None:
    """User-typed code → stored form, or None if it can't be one of ours."""
    code = "".join(ch for ch in raw.upper() if ch.isalpha())
    if len(code) != LENGTH or any(ch not in ALPHABET for ch in code):
        return None
    return code


def create_invite(store: Store, user_id: str, *, now: datetime) -> str:
    for _ in range(5):
        code = _new_code()
        tok = BindToken(
            token=code,
            user_id=user_id,
            kind="invite",
            created_at=now,
            expires_at=int((now + INVITE_TTL).timestamp()),
        )
        try:
            store.put(
                tables.BIND_TOKENS, tok.to_item(), condition="attribute_not_exists(#t)", names={"#t": "token"}
            )
            return code
        except ConditionFailed:  # pragma: no cover - 24^8 codes
            continue
    raise RuntimeError("could not allocate an invite code")  # pragma: no cover


def lookup_invite(store: Store, raw: str, *, now: datetime) -> str | None:
    """The grantee's `user_id`, or None (unknown, expired, used, or malformed — one answer)."""
    code = normalize(raw)
    if code is None:
        return None
    tok = get_bind_token(store, code, now=now, kind="invite")
    return tok.user_id if tok else None


def consume_invite(store: Store, raw: str, user_id: str, *, now: datetime) -> bool:
    code = normalize(raw)
    if code is None:
        return False
    try:
        consume_bind_token(store, code, user_id, now=now, kind="invite")
    except BindTokenRefused:
        return False
    return True
