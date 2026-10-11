"""The resident's own page (04 §3, §6, §9.3): lines, the Watching card per owned line, grants given and
received, invite codes, grant, revoke. (The Watching card's form posts to `routes/watch_settings.py`.)

Every route here needs the page session (set after a successful bind) and every POST a CSRF token. Revocation
exists only here: there is no revoke by voice and no revoke without the page session (04 §3).
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from tower_consent import Grant, list_granted_to, list_grants, list_lines, revoke
from tower_consent import grant as make_grant
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

from binding_page import invite, watching
from binding_page.deps import Deps, PageSession, current_session, get_deps
from binding_page.templating import render

router = APIRouter()

KINDS = ("watch", "reachability")


def grant_id(g: Grant) -> str:
    """`<line_id>:<kind>:<grantee_user_id>` — line ids and kinds hold no `:`, so the grantee is the rest."""
    return f"{g.line_id}:{g.grant}:{g.grantee_user_id}"


def parse_grant_id(value: str) -> tuple[str, str, str] | None:
    parts = unquote(value).split(":", 2)
    if len(parts) != 3 or parts[1] not in KINDS or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


def need_session(request: Request, deps: Deps) -> PageSession | None:
    return current_session(request, deps)


def no_session(request: Request) -> HTMLResponse:
    return render(request, "refused.html", status_code=401, reason="session")


def me_context(deps: Deps, s: PageSession) -> dict[str, Any]:
    lines = list_lines(deps.store, s.user_id)
    given = []
    for line in lines:
        for g in list_grants(deps.store, line.line_id):
            given.append({"g": g, "id": quote(grant_id(g), safe="")})
    return {
        "user_id": s.user_id,
        "lines": lines,
        "given": given,
        "received": list_granted_to(deps.store, s.user_id),
        "watching": [watching.card(deps, line.line_id, s.user_id) for line in lines],
        "contact_slots": range(watching.CONTACT_SLOTS),
        "csrf": deps.signer.csrf(s.raw),
        "kinds": KINDS,
    }


def me_page(
    request: Request, deps: Deps, s: PageSession, *, status_code: int = 200, **extra: Any
) -> HTMLResponse:
    return render(request, "me.html", status_code=status_code, **me_context(deps, s), **extra)


@router.get("/me", response_class=HTMLResponse)
def me(request: Request, deps: Annotated[Deps, Depends(get_deps)]) -> Response:
    s = need_session(request, deps)
    if s is None:
        return no_session(request)
    return me_page(request, deps, s)


@router.post("/me/invite", response_class=HTMLResponse)
def new_invite(
    request: Request, deps: Annotated[Deps, Depends(get_deps)], csrf: Annotated[str, Form()] = ""
) -> Response:
    s = need_session(request, deps)
    if s is None:
        return no_session(request)
    if not deps.signer.check_csrf(s.raw, csrf):
        return render(request, "refused.html", status_code=403, reason="csrf")
    code = invite.create_invite(deps.store, s.user_id, now=deps.now())
    return me_page(request, deps, s, invite_code=invite.display(code))


GRANT_ERRORS: dict[type[Exception], tuple[int, str]] = {
    AliasCollision: (409, "You already use that name for someone else's line on their side. Pick another."),
    GrantExists: (409, "That person already has this permission for this line."),
    InvalidAlias: (422, "Use a short name with letters only, like “mom”."),
    NotGrantable: (422, "Only “watch” or “reachability” can be shared."),
    GrantToSelf: (422, "That invite code is your own."),
    NotLineOwner: (403, "Only the line-holder can share this line."),
    LineNotFound: (403, "Only the line-holder can share this line."),
}


@router.post("/grants", response_class=HTMLResponse)
def create_grant(
    request: Request,
    deps: Annotated[Deps, Depends(get_deps)],
    invite_code: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    alias: Annotated[str, Form()] = "",
    line_id: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> Response:
    s = need_session(request, deps)
    if s is None:
        return no_session(request)
    if not deps.signer.check_csrf(s.raw, csrf):
        return render(request, "refused.html", status_code=403, reason="csrf")
    if not line_id:
        owned = list_lines(deps.store, s.user_id)
        line_id = owned[0].line_id if len(owned) == 1 else ""
    grantee = invite.lookup_invite(deps.store, invite_code, now=deps.now())
    if grantee is None:
        return me_page(request, deps, s, status_code=404, error="That invite code is unknown or has expired.")
    alias = alias.strip().lower()
    try:
        make_grant(deps.store, line_id, grantee, kind, alias, granted_by=s.user_id, now=deps.now())
    except tuple(GRANT_ERRORS) as e:
        status, msg = GRANT_ERRORS[type(e)]
        return me_page(request, deps, s, status_code=status, error=msg)
    invite.consume_invite(deps.store, invite_code, grantee, now=deps.now())
    return RedirectResponse("/me", status_code=303)


@router.post("/grants/{gid}/revoke", response_class=HTMLResponse)
def revoke_grant(
    request: Request,
    gid: str,
    deps: Annotated[Deps, Depends(get_deps)],
    csrf: Annotated[str, Form()] = "",
) -> Response:
    s = need_session(request, deps)
    if s is None:
        return no_session(request)
    if not deps.signer.check_csrf(s.raw, csrf):
        return render(request, "refused.html", status_code=403, reason="csrf")
    parsed = parse_grant_id(gid)
    if parsed is None:
        return me_page(request, deps, s, status_code=404, error="No such permission.")
    line_id, kind, grantee = parsed
    try:
        revoke(deps.store, line_id, grantee, kind, revoked_by=s.user_id, now=deps.now())  # type: ignore[arg-type]
    except (NotLineOwner, LineNotFound):
        return me_page(request, deps, s, status_code=403, error="Only the line-holder can revoke.")
    except GrantNotFound:
        return me_page(request, deps, s, status_code=404, error="That permission was already revoked.")
    return RedirectResponse("/me", status_code=303)
