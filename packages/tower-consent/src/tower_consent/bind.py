"""Bind tokens and line binding (04 §2).

The number is never typed by the user: `bind_line` is called with the E.164 the *carrier* asserted
(Number Verification), stores only `line_id = HMAC(e164)` and `msisdn_enc`, and is idempotent per line.
"""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta

from tower_consent import tables as T
from tower_consent.crypto import LineIdHasher, MsisdnCipher, validate_e164
from tower_consent.errors import BindTokenRefused, ConditionFailed, LineOwnedByOtherUser
from tower_consent.models import BindingMethod, BindToken, Line, TokenKind
from tower_consent.store import Store

BIND_TOKEN_TTL = timedelta(minutes=10)
_TOKEN_ALPHABET = string.ascii_letters  # letters only: a token can never look like a phone number
_TOKEN_LEN = 32  # ~182 bits


def new_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(_TOKEN_LEN))


def create_bind_token(
    store: Store,
    user_id: str,
    *,
    now: datetime,
    ttl: timedelta = BIND_TOKEN_TTL,
    kind: TokenKind = "bind",
) -> BindToken:
    """Single-use token tied to `user_id`; expires `ttl` after `now` (default 10 min)."""
    tok = BindToken(
        token=new_token(), user_id=user_id, kind=kind, created_at=now, expires_at=int((now + ttl).timestamp())
    )
    store.put(T.BIND_TOKENS, tok.to_item(), condition="attribute_not_exists(#t)", names={"#t": "token"})
    return tok


def get_bind_token(store: Store, token: str, *, now: datetime, kind: TokenKind = "bind") -> BindToken | None:
    """Read without consuming (the GET page). Expired, wrong-kind or unknown → None. TTL deletion is lazy,
    so expiry is checked here, not trusted to DynamoDB."""
    item = store.get(T.BIND_TOKENS, {"token": token})
    if item is None:
        return None
    tok = BindToken.model_validate(item)
    if tok.kind != kind or tok.expires_at <= int(now.timestamp()):
        return None
    return tok


def consume_bind_token(
    store: Store, token: str, user_id: str, *, now: datetime, kind: TokenKind = "bind"
) -> BindToken:
    """Atomically delete the token iff it exists, belongs to `user_id`, has `kind`, and has not expired.

    Reuse, expiry, a different user, an unknown token → `BindTokenRefused` (one error, no oracle). A refused
    attempt by a different user does not burn the token.
    """
    try:
        old = store.delete(
            T.BIND_TOKENS,
            {"token": token},
            condition="attribute_exists(#t) AND user_id = :u AND #k = :k AND expires_at > :now",
            names={"#t": "token", "#k": "kind"},
            values={":u": user_id, ":k": kind, ":now": int(now.timestamp())},
        )
    except ConditionFailed:
        raise BindTokenRefused("token is not valid for this user") from None
    if old is None:  # pragma: no cover - the condition makes this unreachable
        raise BindTokenRefused("token is not valid for this user")
    return BindToken.model_validate(old)


def bind_line(
    store: Store,
    hasher: LineIdHasher,
    cipher: MsisdnCipher,
    user_id: str,
    e164: str,
    method: BindingMethod,
    carrier_hint: str | None = None,
    *,
    now: datetime,
) -> Line:
    """Store the carrier-verified line for `user_id`.

    Idempotent on `line_id`: re-binding the same number by the same user returns the existing row unchanged.
    The same number for a different user → `LineOwnedByOtherUser` (conditional write; nothing overwritten).
    """
    validate_e164(e164)
    line_id = hasher.line_id(e164)
    line = Line(
        line_id=line_id,
        msisdn_enc=cipher.encrypt(e164),
        owner_user_id=user_id,
        bound_at=now,
        binding_method=method,
        carrier_hint=carrier_hint,
    )
    try:
        store.put(T.LINES, line.to_item(), condition="attribute_not_exists(line_id)")
        return line
    except ConditionFailed:
        pass
    existing = store.get(T.LINES, {"line_id": line_id})
    if existing is None:  # pragma: no cover - deleted between the two calls
        raise LineOwnedByOtherUser("line could not be bound")
    current = Line.model_validate(existing)
    if current.owner_user_id != user_id:
        raise LineOwnedByOtherUser("this line is bound to another user")
    return current


def get_line(store: Store, line_id: str) -> Line | None:
    item = store.get(T.LINES, {"line_id": line_id})
    return Line.model_validate(item) if item else None


def list_lines(store: Store, owner_user_id: str) -> list[Line]:
    items = store.query(T.LINES, "owner_user_id = :u", {":u": owner_user_id}, index="by_owner")
    return [Line.model_validate(i) for i in items]
