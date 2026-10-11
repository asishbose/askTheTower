"""Bind: token reuse / expiry / other user → refused; same number same user idempotent; other user → refused."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tower_consent import (
    Store,
    bind_line,
    consume_bind_token,
    create_bind_token,
    get_bind_token,
    get_line,
    list_lines,
    resolve,
)
from tower_consent.crypto import LineIdHasher, MsisdnCipher
from tower_consent.errors import BindTokenRefused, InvalidE164, LineOwnedByOtherUser

from tests.privacy.patterns import E164_STRICT

pytestmark = pytest.mark.integration

MOM_E164 = "+15555550123"


def test_token_is_single_use(store: Store, now: datetime) -> None:
    tok = create_bind_token(store, "mom", now=now)
    assert get_bind_token(store, tok.token, now=now) is not None
    assert consume_bind_token(store, tok.token, "mom", now=now).user_id == "mom"
    with pytest.raises(BindTokenRefused):
        consume_bind_token(store, tok.token, "mom", now=now)
    assert get_bind_token(store, tok.token, now=now) is None


def test_token_expires_after_ten_minutes(store: Store, now: datetime) -> None:
    tok = create_bind_token(store, "mom", now=now)
    assert tok.expires_at == int((now + timedelta(minutes=10)).timestamp())
    later = now + timedelta(minutes=10)
    assert get_bind_token(store, tok.token, now=later) is None
    with pytest.raises(BindTokenRefused):
        consume_bind_token(store, tok.token, "mom", now=later)
    # just inside the window still works
    tok2 = create_bind_token(store, "mom", now=now)
    consume_bind_token(store, tok2.token, "mom", now=now + timedelta(minutes=9, seconds=59))


def test_token_for_other_user_refused_and_not_burned(store: Store, now: datetime) -> None:
    tok = create_bind_token(store, "mom", now=now)
    with pytest.raises(BindTokenRefused):
        consume_bind_token(store, tok.token, "asish", now=now)
    consume_bind_token(store, tok.token, "mom", now=now)


def test_unknown_token_and_wrong_kind_refused(store: Store, now: datetime) -> None:
    with pytest.raises(BindTokenRefused):
        consume_bind_token(store, "nope", "mom", now=now)
    invite = create_bind_token(store, "asish", now=now, kind="invite", ttl=timedelta(hours=24))
    with pytest.raises(BindTokenRefused):
        consume_bind_token(store, invite.token, "asish", now=now, kind="bind")
    assert get_bind_token(store, invite.token, now=now, kind="invite") is not None


def test_tokens_are_letters_only_and_redacted_in_repr(store: Store, now: datetime) -> None:
    tok = create_bind_token(store, "mom", now=now)
    assert tok.token.isalpha() and len(tok.token) == 32
    assert tok.token not in repr(tok)
    assert not E164_STRICT.search(tok.token)


def test_bind_stores_hmac_and_ciphertext_only(
    store: Store, hasher: LineIdHasher, cipher: MsisdnCipher, now: datetime
) -> None:
    line = bind_line(store, hasher, cipher, "mom", MOM_E164, "auth_code", "mock", now=now)
    assert line.line_id == hasher.line_id(MOM_E164)
    stored = get_line(store, line.line_id)
    assert stored is not None
    assert cipher.decrypt(stored.msisdn_enc) == MOM_E164
    assert MOM_E164 not in repr(stored) and MOM_E164 not in str(stored.to_item())
    assert resolve(store, "mom", "self").line_id == line.line_id


def test_rebind_same_number_same_user_is_idempotent(
    store: Store, hasher: LineIdHasher, cipher: MsisdnCipher, now: datetime
) -> None:
    first = bind_line(store, hasher, cipher, "mom", MOM_E164, "auth_code", now=now)
    again = bind_line(store, hasher, cipher, "mom", MOM_E164, "ciba", now=now + timedelta(days=1))
    assert again == first  # unchanged row: same bound_at, method and ciphertext
    assert len(list_lines(store, "mom")) == 1


def test_bind_number_owned_by_other_user_refused(
    store: Store, hasher: LineIdHasher, cipher: MsisdnCipher, now: datetime
) -> None:
    first = bind_line(store, hasher, cipher, "mom", MOM_E164, "auth_code", now=now)
    with pytest.raises(LineOwnedByOtherUser) as ei:
        bind_line(store, hasher, cipher, "attacker", MOM_E164, "auth_code", now=now)
    assert MOM_E164 not in str(ei.value)
    assert get_line(store, first.line_id) == first
    assert not resolve(store, "attacker", "self").view.bound


def test_bind_rejects_non_e164(
    store: Store, hasher: LineIdHasher, cipher: MsisdnCipher, now: datetime
) -> None:
    with pytest.raises(InvalidE164):
        bind_line(store, hasher, cipher, "mom", "5555550123", "auth_code", now=now)


def test_self_resolves_to_newest_line(
    store: Store, hasher: LineIdHasher, cipher: MsisdnCipher, now: datetime
) -> None:
    bind_line(store, hasher, cipher, "mom", MOM_E164, "auth_code", now=now)
    newer = bind_line(store, hasher, cipher, "mom", "+15555550199", "auth_code", now=now + timedelta(hours=1))
    assert resolve(store, "mom", "self").line_id == newer.line_id
