"""The showcase's "show the tables" view (04 §8, testing-and-showcase §2.4). Off by default.

Served only when `BIND_ADMIN=1` **and** (`TOWER_ENV=local` **or** `BIND_ADMIN_TOKEN` is set); otherwise every
`/_admin/*` path is 404, whatever its query or body. Numbers are redacted by construction: the tables never hold
one (`line_id` is an HMAC, `msisdn_enc` ciphertext, which this view shortens anyway).

**Bearer mode** (`BIND_ADMIN_TOKEN` set; on AWS only while the demo admin path is open, 10 §2.1): every request
needs `Authorization: Bearer <token>` (401 otherwise), `GET /_admin/tables` answers only `?table=Grants` (else
422) and `POST /_admin/watch-settings` is 404. That is what the demo UI's pane 4 and its AWS Reset use.

- `GET  /_admin/tables`                    every row of the six tables (HTML; `?format=json` for scripts);
                                           `?table=Grants` (any one table name) narrows it to that table — the
                                           demo UI's pane 4 reads only Grants, never another line's Audit rows
- `GET  /_admin/resolve?user=..&line=..`   `tower_consent.resolve` as JSON — the showcase's "resolve flips"
- `POST /_admin/bind-tokens {user_id}`     a fresh bind link for the showcase QR code / the compose seed
- `POST /_admin/grants {owner_user_id, grantee_user_id, grant, alias, action}`
                                           grant or revoke on the owner's line, idempotent — the compose seed and
                                           `ref-client demo` (moment 3's revoke and re-grant) use it instead of
                                           driving the page session; the resident's own path stays `/me`
- `POST /_admin/watch-settings {owner_user_id, profile, contacts, line_id?, reset?}`
                                           the line-holder's profile and contacts through the same
                                           `watching.save` the `/me` form uses (04 §9.2); `reset: true` deletes
                                           the owner's Watch so a demo re-run starts clean
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from tower_consent import create_bind_token, delete_watch, list_grants, list_lines, resolve, revoke, tables
from tower_consent import grant as make_grant
from tower_consent.errors import ConsentError

from binding_page import watching
from binding_page.deps import Deps, get_deps
from binding_page.templating import render


def admin_gate(
    deps: Annotated[Deps, Depends(get_deps)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """404 when the admin view is off (before anything else is looked at); 401 in bearer mode without the bearer."""
    settings = deps.settings
    if not settings.admin_enabled:
        raise HTTPException(status_code=404)
    if settings.admin_bearer_mode:
        expected = f"Bearer {settings.admin_token}"
        if authorization is None or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="unauthorized")


router = APIRouter(prefix="/_admin", dependencies=[Depends(admin_gate)])

# Bearer mode serves only the Grants table (04 §8): pane 4 never needs another line's Audit rows.
_BEARER_TABLES = frozenset({"Grants"})

_ENC_ATTRS = {"msisdn_enc", "alert_phone_enc"}
_EPOCH_ATTRS = {t.ttl_attribute for t in tables.TABLES if t.ttl_attribute}


def _redact(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    for k in _ENC_ATTRS & out.keys():
        out[k] = f"{str(out[k])[:10]}… (ciphertext)"
    for k in _EPOCH_ATTRS & out.keys():  # TTL epochs are 10 digits: show them as times, not phone-shaped runs
        if isinstance(out[k], int):
            out[k] = datetime.fromtimestamp(out[k], UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if "token" in out and isinstance(out["token"], str) and out.get("kind") == "bind":
        out["token"] = f"{out['token'][:4]}…"
    return out


_TABLE_NAMES = frozenset(t.name for t in tables.TABLES)


def dump(deps: Deps, only: str | None = None) -> dict[str, list[dict[str, Any]]]:
    return {
        t.name: [_redact(i) for i in deps.store.scan_all(t)]
        for t in tables.TABLES
        if only is None or t.name == only
    }


@router.get("/tables", response_model=None)
def admin_tables(request: Request, deps: Annotated[Deps, Depends(get_deps)]) -> Response:
    only = request.query_params.get("table")
    allowed = _BEARER_TABLES if deps.settings.admin_bearer_mode else _TABLE_NAMES
    if (only is None and deps.settings.admin_bearer_mode) or (only is not None and only not in allowed):
        raise HTTPException(status_code=422, detail=f"table must be one of {sorted(allowed)}")
    data = dump(deps, only)
    if request.query_params.get("format") == "json":
        return JSONResponse(data)
    cols = {name: sorted({k for row in rows for k in row}) for name, rows in data.items()}
    return render(request, "admin.html", data=data, cols=cols)


@router.get("/resolve")
def admin_resolve(user: str, line: str, deps: Annotated[Deps, Depends(get_deps)]) -> dict[str, Any]:
    r = resolve(deps.store, user, line)
    return {"line_id": r.line_id, **r.view.model_dump(mode="json")}


@router.post("/bind-tokens")
def admin_bind_token(
    user_id: Annotated[str, Body(embed=True)], deps: Annotated[Deps, Depends(get_deps)]
) -> dict[str, str]:
    tok = create_bind_token(deps.store, user_id, now=deps.now())
    return {"url": f"{deps.settings.base_url}/bind/{tok.token}"}


class AdminGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str
    grantee_user_id: str
    grant: Literal["watch", "reachability"]
    alias: str
    action: Literal["grant", "revoke"]
    line_id: str | None = None  # needed only when the owner holds more than one line


def _owners_line(deps: Deps, owner_user_id: str, line_id: str | None) -> str:
    """The owner's only line, or `line_id` if they hold it; 409 otherwise."""
    owned: list[str] = [ln.line_id for ln in list_lines(deps.store, owner_user_id)]
    if line_id is not None:
        if line_id not in owned:
            raise HTTPException(status_code=409, detail="the owner holds no such line")
        return line_id
    if len(owned) == 1:
        return owned[0]
    raise HTTPException(
        status_code=409, detail=f"the owner holds {len(owned)} lines; bind first or pass line_id"
    )


@router.post("/grants")
def admin_grant(body: AdminGrant, deps: Annotated[Deps, Depends(get_deps)]) -> dict[str, Any]:
    """Idempotent: granting an active grant or revoking a revoked/absent one changes nothing (`changed: false`).
    The same `tower_consent.grant` / `revoke` the resident's `/me` page calls, acting as the owner."""
    line_id = _owners_line(deps, body.owner_user_id, body.line_id)
    active = any(
        g.grantee_user_id == body.grantee_user_id and g.grant == body.grant
        for g in list_grants(deps.store, line_id, include_revoked=False)
    )
    changed = False
    try:
        if body.action == "grant" and not active:
            make_grant(
                deps.store,
                line_id,
                body.grantee_user_id,
                body.grant,
                body.alias.strip().lower(),
                granted_by=body.owner_user_id,
                now=deps.now(),
            )
            changed = True
        elif body.action == "revoke" and active:
            revoke(
                deps.store,
                line_id,
                body.grantee_user_id,
                body.grant,
                revoked_by=body.owner_user_id,
                now=deps.now(),
            )
            changed = True
    except ConsentError as e:
        raise HTTPException(status_code=409, detail=type(e).__name__) from None
    return {
        "line_id": line_id,
        "grantee_user_id": body.grantee_user_id,
        "grant": body.grant,
        "active": body.action == "grant",
        "changed": changed,
    }


class AdminWatchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_user_id: str
    profile: str
    contacts: list[str] = []
    line_id: str | None = None  # needed only when the owner holds more than one line
    reset: bool = False


@router.post("/watch-settings")
def admin_watch_settings(
    body: AdminWatchSettings, deps: Annotated[Deps, Depends(get_deps)]
) -> dict[str, Any]:
    """The `/me` form's save, acting as the line-holder (same validation, same audit row, same Alerts call).
    `reset: true` only deletes the owner's Watch (no audit row: a demo reset, not a resident's choice)."""
    if deps.settings.admin_bearer_mode:  # the Transplant story stays local (04 §8)
        raise HTTPException(status_code=404)
    line_id = _owners_line(deps, body.owner_user_id, body.line_id)
    if body.reset:
        return {"line_id": line_id, "reset": delete_watch(deps.store, line_id, body.owner_user_id)}
    try:
        saved = watching.save(deps, line_id, body.owner_user_id, body.profile, body.contacts)
    except ConsentError as e:
        raise HTTPException(status_code=422, detail=type(e).__name__) from None
    w = saved.watch
    return {
        "line_id": line_id,
        "profile": w.profile,
        "contacts": [s.user_id for s in w.escalation],
        "enabled": w.enabled,
        "audited": saved.audited,
    }
