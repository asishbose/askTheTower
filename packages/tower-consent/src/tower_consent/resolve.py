"""`resolve(user_id, line)` — the single DynamoDB read on the hot path (04 §5, e2e-wiring §2).

- `"self"`  → one Query on `Lines.by_owner`, Limit 1, newest binding first; grant = `owner`. No line →
  `bound=False` (the engine says `NOT_BOUND`: bind *your* line — the only path that mints a bind token).
- an alias → one Query on `Grants.by_grantee` filtered on `alias`. An unrevoked grant → its kind. Only revoked
  grants under that alias → `bound=True, grant="none", revoked_at=<latest>, line_id` so the engine says
  `NO_CONSENT` and the refused attempt is audited on that line. No grant at all → `bound=True, grant="none"`,
  no `line_id`: also `NO_CONSENT`, nothing to audit (D18 — it is not the caller's own line that is unbound).
- anything that is not a valid alias (e.g. something number-shaped) → the same no-grant answer, with **zero**
  requests.

Never raises for "not found"; never caches.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from tower_policy.types import ConsentView

from tower_consent import tables as T
from tower_consent.errors import InvalidAlias
from tower_consent.models import Grant, Line, validate_alias
from tower_consent.store import Store

SELF = "self"
_RANK = {"watch": 2, "reachability": 1}


class ResolvedConsent(BaseModel):
    model_config = ConfigDict(frozen=True)

    view: ConsentView
    line_id: str | None


def _none(
    bound: bool = True, line_id: str | None = None, revoked_at: datetime | None = None
) -> ResolvedConsent:
    return ResolvedConsent(
        view=ConsentView(bound=bound, grant="none", revoked_at=revoked_at, line_id=line_id), line_id=line_id
    )


def normalise_line(line: str) -> str:
    return line.strip().lower()


def resolve(store: Store, user_id: str, line: str) -> ResolvedConsent:
    line = normalise_line(line)
    if line == SELF:
        items, _ = store.query_page(
            T.LINES,
            "owner_user_id = :u",
            {":u": user_id},
            index="by_owner",
            limit=1,
            forward=False,
        )
        if not items:
            return _none(bound=False)
        owned = Line.model_validate(items[0])
        return ResolvedConsent(
            view=ConsentView(bound=True, grant="owner", line_id=owned.line_id), line_id=owned.line_id
        )

    try:
        alias = validate_alias(line)
    except InvalidAlias:
        return _none()
    items, _ = store.query_page(
        T.GRANTS,
        "grantee_user_id = :u",
        {":u": user_id, ":a": alias},
        index="by_grantee",
        filter_expression="#alias = :a",
        names={"#alias": "alias"},
    )
    grants = [Grant.model_validate(i) for i in items]
    active = sorted((g for g in grants if g.active), key=lambda g: _RANK[g.grant], reverse=True)
    if active:
        g = active[0]
        return ResolvedConsent(
            view=ConsentView(bound=True, grant=g.grant, line_id=g.line_id), line_id=g.line_id
        )
    if grants:
        latest = max(grants, key=lambda g: g.revoked_at or g.granted_at)
        return _none(line_id=latest.line_id, revoked_at=latest.revoked_at)
    return _none()  # no grant under this alias: NO_CONSENT, never NOT_BOUND (D18)
