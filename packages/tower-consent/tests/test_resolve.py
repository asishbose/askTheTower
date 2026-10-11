"""resolve: self → owner; alias → grant kind; revoked → none; unknown alias → bound False; exactly one request."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tower_consent import Store, bind_line, grant, resolve, revoke
from tower_consent.crypto import LineIdHasher, MsisdnCipher

pytestmark = pytest.mark.integration

MOM_E164 = "+15555550123"


@pytest.fixture
def mom_line(store: Store, hasher: LineIdHasher, cipher: MsisdnCipher, now: datetime) -> str:
    return bind_line(store, hasher, cipher, "mom", MOM_E164, "auth_code", "mock", now=now).line_id


def test_self_resolves_to_owner_in_one_query(store: Store, mom_line: str, counting) -> None:  # type: ignore[no-untyped-def]
    with counting() as c:
        r = resolve(store, "mom", "self")
    assert c.calls == ["Query"]
    assert r.view.bound and r.view.grant == "owner" and r.view.revoked_at is None
    assert r.line_id == mom_line == r.view.line_id


def test_self_unbound_is_bound_false(store: Store, counting) -> None:  # type: ignore[no-untyped-def]
    with counting() as c:
        r = resolve(store, "nobody", "self")
    assert c.calls == ["Query"]
    assert not r.view.bound and r.view.grant == "none" and r.line_id is None


@pytest.mark.parametrize("kind", ["watch", "reachability"])
def test_alias_resolves_to_grant_kind_in_one_query(
    store: Store, mom_line: str, now: datetime, counting, kind: str
) -> None:  # type: ignore[no-untyped-def]
    grant(store, mom_line, "asish", kind, "mom", granted_by="mom", now=now)
    with counting() as c:
        r = resolve(store, "asish", "mom")
    assert c.calls == ["Query"]
    assert r.view.bound and r.view.grant == kind and r.line_id == mom_line


def test_alias_is_normalised(store: Store, mom_line: str, now: datetime) -> None:
    grant(store, mom_line, "asish", "watch", "mom", granted_by="mom", now=now)
    assert resolve(store, "asish", "  Mom ").view.grant == "watch"


def test_grant_then_revoke_flips_to_none_on_next_call(
    store: Store, mom_line: str, now: datetime, counting
) -> None:  # type: ignore[no-untyped-def]
    grant(store, mom_line, "asish", "watch", "mom", granted_by="mom", now=now)
    assert resolve(store, "asish", "mom").view.grant == "watch"
    revoke(store, mom_line, "asish", "watch", revoked_by="mom", now=now + timedelta(minutes=1))
    with counting() as c:
        r = resolve(store, "asish", "mom")
    assert c.calls == ["Query"]
    assert r.view.bound and r.view.grant == "none"
    assert r.view.revoked_at == now + timedelta(minutes=1)
    assert r.line_id == mom_line  # kept so the refused attempt is audited on Mom's line


def test_regrant_after_revoke_flips_back(store: Store, mom_line: str, now: datetime) -> None:
    grant(store, mom_line, "asish", "watch", "mom", granted_by="mom", now=now)
    revoke(store, mom_line, "asish", "watch", revoked_by="mom", now=now)
    grant(store, mom_line, "asish", "reachability", "mom", granted_by="mom", now=now + timedelta(hours=1))
    assert resolve(store, "asish", "mom").view.grant == "reachability"


def test_unknown_alias_is_no_consent(store: Store, mom_line: str, now: datetime, counting) -> None:  # type: ignore[no-untyped-def]
    """04 §5 / D18: no grant under the alias → `grant="none"`, no line_id (NO_CONSENT, never NOT_BOUND)."""
    grant(store, mom_line, "asish", "watch", "mom", granted_by="mom", now=now)
    with counting() as c:
        r = resolve(store, "asish", "dad")
    assert c.calls == ["Query"]
    assert r.view.bound and r.view.grant == "none" and r.line_id is None and r.view.revoked_at is None


def test_alias_of_another_grantee_is_not_visible(store: Store, mom_line: str, now: datetime) -> None:
    grant(store, mom_line, "asish", "watch", "mom", granted_by="mom", now=now)
    r = resolve(store, "stranger", "mom")
    assert r.view.grant == "none" and r.line_id is None and r.view.line_id is None  # nothing of Mom's line


@pytest.mark.parametrize("line", ["+15555550123", "15555550123", "x" * 30, "", "self2"])
def test_number_shaped_or_invalid_line_makes_no_request(
    store: Store, mom_line: str, counting, line: str
) -> None:  # type: ignore[no-untyped-def]
    with counting() as c:
        r = resolve(store, "mom", line)
    assert c.calls == []
    assert r.view.grant == "none" and r.line_id is None  # D18: NO_CONSENT, nothing to audit


def test_resolve_has_no_cache(store: Store, mom_line: str, now: datetime, counting) -> None:  # type: ignore[no-untyped-def]
    grant(store, mom_line, "asish", "watch", "mom", granted_by="mom", now=now)
    with counting() as c:
        for _ in range(3):
            resolve(store, "asish", "mom")
    assert c.calls == ["Query"] * 3
