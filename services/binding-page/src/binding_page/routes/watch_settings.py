"""`POST /me/lines/{line_id}/watch-settings` — the line-holder's profile and contacts (04 §9.2).

Page session + CSRF like every `/me` route. Contacts arrive as `contact_1`…`contact_N` in order; blanks are
dropped and anything past three is refused by `tower_consent` (422). Every refusal writes nothing. The Watching
card itself is part of `GET /me` (`routes/grants.py` → `watching.card`).
"""

from __future__ import annotations

import re
from typing import Annotated

import anyio
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from tower_consent.errors import InvalidContacts, InvalidProfile, LineNotFound, NoContact, NotLineOwner

from binding_page import watching
from binding_page.deps import Deps, get_deps
from binding_page.routes.grants import me_page, need_session, no_session
from binding_page.templating import render

router = APIRouter()

CONTACT_FIELD = re.compile(r"^contact_(\d{1,2})$")

# status and the template's message key (`me.html` holds the words)
SETTINGS_ERRORS: dict[type[Exception], tuple[int, str]] = {
    NotLineOwner: (403, "not_owner"),
    LineNotFound: (403, "not_owner"),
    InvalidProfile: (422, "profile"),
    InvalidContacts: (422, "contacts"),
    NoContact: (422, "no_contact"),
}
STORE_ERRORS = (BotoCoreError, ClientError)


def contacts_from_form(fields: dict[str, str]) -> list[str]:
    numbered = sorted(
        (int(m.group(1)), v.strip()) for k, v in fields.items() if (m := CONTACT_FIELD.match(k))
    )
    return [v for _, v in numbered if v]


@router.post("/me/lines/{line_id}/watch-settings", response_class=HTMLResponse)
async def save_watch_settings(
    request: Request, line_id: str, deps: Annotated[Deps, Depends(get_deps)]
) -> Response:
    s = need_session(request, deps)
    if s is None:
        return no_session(request)
    form = await request.form()
    fields = {k: v for k, v in form.items() if isinstance(v, str)}
    if not deps.signer.check_csrf(s.raw, fields.get("csrf", "")):
        return render(request, "refused.html", status_code=403, reason="csrf")
    profile = watching.profile_from_form(fields.get("profile", ""))
    contacts = contacts_from_form(fields)
    try:
        saved = await anyio.to_thread.run_sync(
            lambda: watching.save(deps, line_id, s.user_id, profile, contacts)
        )
    except tuple(SETTINGS_ERRORS) as e:
        status, key = SETTINGS_ERRORS[type(e)]
        page = await anyio.to_thread.run_sync(
            lambda: me_page(request, deps, s, status_code=status, settings_error=key)
        )
        return page
    except STORE_ERRORS:
        return render(request, "refused.html", status_code=503, reason="unavailable")
    if not saved.audited:
        return render(request, "refused.html", status_code=500, reason="audit")
    return RedirectResponse("/me", status_code=303)
