"""Grants: grant/revoke/flip; alias collision; grant to self rejected; "owner" not grantable."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tower_consent import Store, bind_line, grant, list_granted_to, list_grants, resolve, revoke
from tower_consent.crypto import LineIdHasher, MsisdnCipher
from tower_consent.errors import (
    AliasCollision,
    GrantExists,
    GrantNotFound,
    GrantToSelf,
    InvalidAlias,
    LineNotFound,
    NotGrantable,
    NotLineOwner,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def lines(store: Store, hasher: LineIdHasher, cipher: MsisdnCipher, now: datetime) -> dict[str, str]:
    mom = bind_line(store, hasher, cipher, "mom", "+15555550123", "auth_code", now=now).line_id
    dad = bind_line(store, hasher, cipher, "dad", "+15555550124", "auth_code", now=now).line_id
    return {"mom": mom, "dad": dad}


def test_grant_revoke_flip(store: Store, lines: dict[str, str], now: datetime) -> None:
    g = grant(store, lines["mom"], "asish", "watch", "mom", granted_by="mom", now=now)
    assert g.active and g.grant == "watch"
    assert [x.alias for x in list_grants(store, lines["mom"])] == ["mom"]
    assert [x.line_id for x in list_granted_to(store, "asish")] == [lines["mom"]]
    assert resolve(store, "asish", "mom").view.grant == "watch"

    r = revoke(store, lines["mom"], "asish", "watch", revoked_by="mom", now=now + timedelta(seconds=1))
    assert not r.active
    assert resolve(store, "asish", "mom").view.grant == "none"
    assert list_granted_to(store, "asish") == []
    assert len(list_granted_to(store, "asish", include_revoked=True)) == 1
    assert list_grants(store, lines["mom"], include_revoked=False) == []

    grant(store, lines["mom"], "asish", "watch", "mom", granted_by="mom", now=now + timedelta(seconds=2))
    assert resolve(store, "asish", "mom").view.grant == "watch"


def test_alias_collision_rejected(store: Store, lines: dict[str, str], now: datetime) -> None:
    grant(store, lines["mom"], "asish", "watch", "mom", granted_by="mom", now=now)
    with pytest.raises(AliasCollision):
        grant(store, lines["dad"], "asish", "reachability", "mom", granted_by="dad", now=now)
    # same alias, different grantee: fine
    grant(store, lines["dad"], "priya", "reachability", "mom", granted_by="dad", now=now)
    # after a revoke the alias is free again
    revoke(store, lines["mom"], "asish", "watch", revoked_by="mom", now=now)
    grant(store, lines["dad"], "asish", "reachability", "mom", granted_by="dad", now=now)
    assert resolve(store, "asish", "mom").line_id == lines["dad"]


def test_same_line_second_alias_for_same_kind_is_a_duplicate(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    grant(store, lines["mom"], "asish", "watch", "mom", granted_by="mom", now=now)
    with pytest.raises(GrantExists):
        grant(store, lines["mom"], "asish", "watch", "mother", granted_by="mom", now=now)


def test_grant_to_self_rejected(store: Store, lines: dict[str, str], now: datetime) -> None:
    with pytest.raises(GrantToSelf):
        grant(store, lines["mom"], "mom", "watch", "me too", granted_by="mom", now=now)


@pytest.mark.parametrize("kind", ["owner", "none", "admin", "WATCH"])
def test_only_watch_and_reachability_grantable(
    store: Store, lines: dict[str, str], now: datetime, kind: str
) -> None:
    with pytest.raises(NotGrantable):
        grant(store, lines["mom"], "asish", kind, "mom", granted_by="mom", now=now)


def test_only_the_owner_can_grant_or_revoke(store: Store, lines: dict[str, str], now: datetime) -> None:
    with pytest.raises(NotLineOwner):
        grant(store, lines["mom"], "asish", "watch", "mom", granted_by="asish", now=now)
    grant(store, lines["mom"], "asish", "watch", "mom", granted_by="mom", now=now)
    with pytest.raises(NotLineOwner):
        revoke(store, lines["mom"], "asish", "watch", revoked_by="asish", now=now)
    with pytest.raises(LineNotFound):
        grant(store, "ln_" + "a" * 64, "asish", "watch", "mom", granted_by="mom", now=now)


def test_revoke_twice_or_unknown(store: Store, lines: dict[str, str], now: datetime) -> None:
    with pytest.raises(GrantNotFound):
        revoke(store, lines["mom"], "asish", "watch", revoked_by="mom", now=now)
    grant(store, lines["mom"], "asish", "watch", "mom", granted_by="mom", now=now)
    revoke(store, lines["mom"], "asish", "watch", revoked_by="mom", now=now)
    with pytest.raises(GrantNotFound):
        revoke(store, lines["mom"], "asish", "watch", revoked_by="mom", now=now)


@pytest.mark.parametrize("alias", ["Mom", "mom2", "a" * 25, " mom", "self", "", "mom\n", "+15555550123"])
def test_alias_validation(store: Store, lines: dict[str, str], now: datetime, alias: str) -> None:
    with pytest.raises(InvalidAlias):
        grant(store, lines["mom"], "asish", "watch", alias, granted_by="mom", now=now)


def test_valid_aliases(store: Store, lines: dict[str, str], now: datetime) -> None:
    g = grant(store, lines["mom"], "asish", "watch", "big sister o'neil", granted_by="mom", now=now)
    assert resolve(store, "asish", "big sister o'neil").view.grant == "watch"
    assert len(g.alias) <= 24
