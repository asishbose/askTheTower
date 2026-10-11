"""Grants: `watch` or `reachability`, to a named user, under an alias the line-holder chooses (04 §3).

There is no `owner` grant (ownership is binding) and no revoke-by-voice — `revoke` is called by the binding page.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from tower_consent import tables as T
from tower_consent.errors import (
    AliasCollision,
    ConditionFailed,
    GrantExists,
    GrantNotFound,
    GrantToSelf,
    LineNotFound,
    NotGrantable,
    NotLineOwner,
)
from tower_consent.models import Grant, GrantKind, Line, format_ts, validate_alias
from tower_consent.store import Store
from tower_consent.watches import disable_watch, remove_contact

GRANTABLE: frozenset[str] = frozenset({"watch", "reachability"})


def owned_line(store: Store, line_id: str, acting_user_id: str) -> Line:
    """The line, if `acting_user_id` holds it. Grants, revokes and watch settings are the line-holder's alone."""
    item = store.get(T.LINES, {"line_id": line_id})
    if item is None:
        raise LineNotFound("no such line")
    line = Line.model_validate(item)
    if line.owner_user_id != acting_user_id:
        raise NotLineOwner("only the line-holder can change this line's grants or watch settings")
    return line


def grant(
    store: Store,
    line_id: str,
    grantee_user_id: str,
    kind: str,
    alias: str,
    *,
    granted_by: str,
    now: datetime,
) -> Grant:
    """Create (or re-create after a revoke) a grant. `granted_by` must own the line.

    Rejects: `owner`/unknown kinds, grant to self, an alias the grantee already uses for an unrevoked grant,
    and a duplicate unrevoked grant of the same kind (conditional write).
    """
    if kind not in GRANTABLE:
        raise NotGrantable("only 'watch' and 'reachability' can be granted")
    alias = validate_alias(alias)
    line = owned_line(store, line_id, granted_by)
    if grantee_user_id == line.owner_user_id:
        raise GrantToSelf("the line-holder already owns this line")

    clash = store.query(
        T.GRANTS,
        "grantee_user_id = :u",
        {":u": grantee_user_id, ":a": alias},
        index="by_grantee",
        filter_expression="#alias = :a AND attribute_not_exists(revoked_at)",
        names={"#alias": "alias"},
    )
    if clash:
        raise AliasCollision("the grantee already has a line under this alias")

    g = Grant(
        line_id=line_id,
        grantee_user_id=grantee_user_id,
        grant=cast(GrantKind, kind),
        alias=alias,
        granted_at=now,
    )
    try:
        store.put(
            T.GRANTS,
            g.to_item(),
            condition="attribute_not_exists(line_id) OR attribute_exists(revoked_at)",
        )
    except ConditionFailed:
        raise GrantExists("this grant already exists") from None
    return g


def revoke(
    store: Store, line_id: str, grantee_user_id: str, kind: GrantKind, *, revoked_by: str, now: datetime
) -> Grant:
    """Mark a grant revoked (kept for the record; `resolve` then returns `grant="none"`).

    Revoking `watch` also disables the grantee's Watch on this line (D7, design rule 4): only a `watch` grant
    covers alerts (04 §3), so without it the Watch must stop. It is disabled, not deleted, right after
    `revoked_at` is set; the line-holder's own Watch and other grantees' Watches are untouched. If this second
    write fails, Alerts still ignores the Watch (it re-reads the grant, 06 §4) and drops the subscriptions.
    It also removes the grantee from the line-holder's contact chain (04 §9.4); if that write fails, Alerts
    skips the contact at send time (06 §11.2).
    """
    line = owned_line(store, line_id, revoked_by)
    try:
        item = store.update(
            T.GRANTS,
            {"line_id": line_id, "grantee_grant": f"{grantee_user_id}#{kind}"},
            "SET revoked_at = :now",
            condition="attribute_exists(line_id) AND attribute_not_exists(revoked_at)",
            values={":now": format_ts(now)},
        )
    except ConditionFailed:
        raise GrantNotFound("no active grant to revoke") from None
    if kind == "watch":
        disable_watch(store, line_id, grantee_user_id)
        remove_contact(store, line_id, line.owner_user_id, grantee_user_id)
    return Grant.model_validate(item)


def list_grants(store: Store, line_id: str, *, include_revoked: bool = True) -> list[Grant]:
    """Every grant on one line (the line-holder's view)."""
    grants = [Grant.model_validate(i) for i in store.query(T.GRANTS, "line_id = :l", {":l": line_id})]
    return grants if include_revoked else [g for g in grants if g.active]


def list_granted_to(store: Store, user_id: str, *, include_revoked: bool = False) -> list[Grant]:
    """Every grant a user has received (the grantee's view)."""
    items = store.query(T.GRANTS, "grantee_user_id = :u", {":u": user_id}, index="by_grantee")
    grants = [Grant.model_validate(i) for i in items]
    return grants if include_revoked else [g for g in grants if g.active]
