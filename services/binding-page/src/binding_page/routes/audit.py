"""GET /me/lines/{line_id}/audit — the line-holder's full log with the chain status (07 §4).

Owner only: a watcher asking for the line they watch gets 403 (they never see who else checked).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from tower_audit import AuditAccessDenied, list_for_line, verify

from binding_page.deps import Deps, current_session, get_deps
from binding_page.templating import render

router = APIRouter()


@router.get("/me/lines/{line_id}/audit", response_class=HTMLResponse)
def line_audit(request: Request, line_id: str, deps: Annotated[Deps, Depends(get_deps)]) -> Response:
    s = current_session(request, deps)
    if s is None:
        return render(request, "refused.html", status_code=401, reason="session")
    try:
        rows = list_for_line(deps.store, line_id, s.user_id)
    except AuditAccessDenied:
        return render(request, "refused.html", status_code=403, reason="not_owner")
    chain = verify(deps.store, line_id, deps.audit_signer)
    return render(request, "audit.html", line_id=line_id, rows=rows, chain=chain)
