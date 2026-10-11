"""The one tap (04 §2, e2e-wiring §5 Path C).

GET  /bind/{token}         page: "turn Wi-Fi off, tap Verify" — nothing to type
POST /bind/{token}/verify  start the carrier auth-code flow (state signed with the token's user_id)
GET  /bind/callback        exchange the code → Number Verification (`phoneNumberShare`) → consume the token →
                           `bind_line` → "Line connected" (masked last four) and a page session
"""

from __future__ import annotations

from typing import Annotated

from camara_client import CarrierError
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from tower_consent import bind_line, consume_bind_token, ensure_user, get_bind_token
from tower_consent.errors import BindTokenRefused, LineOwnedByOtherUser
from tower_policy import ReasonCode

from binding_page import mobile_data
from binding_page.carrier import authorization_url
from binding_page.deps import Deps, get_deps
from binding_page.session import FLOW_COOKIE, SESSION_COOKIE, nonce
from binding_page.templating import render

router = APIRouter()

FLOW_TTL_S = 600

# Refusal pages: (status, message key). The template holds the words.
EXPIRED = (404, "expired")
WIFI = (403, "wifi")
FLOW = (400, "flow")
TAKEN = (409, "taken")
CARRIER = (502, "carrier")
BAD_SIM = (400, "bad_param")


def refused(request: Request, why: tuple[int, str]) -> HTMLResponse:
    resp = render(request, "refused.html", status_code=why[0], reason=why[1])
    resp.delete_cookie(FLOW_COOKIE, path="/bind")
    return resp


def _sim_param(request: Request, deps: Deps, value: str | None) -> str | None:
    """Validated `?as=` (local) or None (AWS: ignored)."""
    return mobile_data.simulated_client_id(deps.settings, value)


def bind_page(request: Request, token: str, deps: Annotated[Deps, Depends(get_deps)]) -> Response:
    try:
        sim = _sim_param(request, deps, request.query_params.get("as"))
    except mobile_data.SimulationParamRejected:
        return refused(request, BAD_SIM)
    if get_bind_token(deps.store, token, now=deps.now()) is None:
        return refused(request, EXPIRED)
    return render(request, "bind.html", token=token, sim=sim, local=deps.settings.local)


@router.post("/bind/{token}/verify")
async def bind_verify(
    request: Request,
    token: str,
    deps: Annotated[Deps, Depends(get_deps)],
    as_: Annotated[str | None, Form(alias="as")] = None,
) -> Response:
    try:
        sim = _sim_param(request, deps, as_)
    except mobile_data.SimulationParamRejected:
        return refused(request, BAD_SIM)
    tok = get_bind_token(deps.store, token, now=deps.now())
    if tok is None:
        return refused(request, EXPIRED)
    n = nonce()
    exp = deps.epoch() + FLOW_TTL_S
    state = deps.signer.sign("state", {"u": tok.user_id, "n": n}, expires_at=exp)
    flow = deps.signer.sign("flow", {"t": token, "n": n}, expires_at=exp)
    url = authorization_url(deps.carrier_config, deps.carrier, deps.settings.redirect_uri, state)
    try:
        target = await mobile_data.start(deps.settings, deps.carrier_http, url, sim)
    except (mobile_data.CarrierStartFailed, OSError):
        return refused(request, CARRIER)
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        FLOW_COOKIE,
        flow,
        max_age=FLOW_TTL_S,
        path="/bind",
        httponly=True,
        samesite="lax",  # the carrier's redirect back is a top-level cross-site GET
        secure=deps.settings.secure_cookies,
    )
    return resp


def _mask(e164: str) -> str:
    return f"•••• {e164[-4:]}"


@router.get("/bind/callback", response_class=HTMLResponse)
async def bind_callback(request: Request, deps: Annotated[Deps, Depends(get_deps)]) -> Response:
    q = request.query_params
    now = deps.epoch()
    state = deps.signer.unsign("state", q.get("state"), now=now)
    flow = deps.signer.unsign("flow", request.cookies.get(FLOW_COOKIE), now=now)
    if state is None or flow is None or state.get("n") != flow.get("n"):
        return refused(request, FLOW)
    user_id, token = state["u"], flow["t"]
    if not isinstance(user_id, str) or not isinstance(token, str):
        return refused(request, FLOW)
    if q.get("error") or not q.get("code"):
        return refused(request, WIFI)
    try:
        result = await deps.carrier.number_verify(q["code"], redirect_uri=deps.settings.redirect_uri)
    except CarrierError as e:
        if e.reason_code == ReasonCode.NOT_BOUND:
            return refused(request, WIFI)  # not on the line's mobile data: the carrier could not attribute it
        if e.status is not None and 400 <= e.status < 500:
            return refused(request, FLOW)  # code already used / expired
        return refused(request, CARRIER)
    if not result.verified or not result.e164:
        return refused(request, WIFI)
    try:
        consume_bind_token(deps.store, token, user_id, now=deps.now())
    except BindTokenRefused:
        return refused(request, EXPIRED)
    ensure_user(deps.store, user_id, now=deps.now())
    try:
        bind_line(
            deps.store,
            deps.hasher,
            deps.cipher,
            user_id,
            result.e164,
            "auth_code",
            carrier_hint=deps.carrier_config.backend,
            now=deps.now(),
        )
    except LineOwnedByOtherUser:
        return refused(request, TAKEN)
    masked = _mask(result.e164)
    session = deps.signer.sign("session", {"u": user_id}, expires_at=now + deps.settings.session_ttl_s)
    resp = render(request, "connected.html", masked=masked)
    resp.delete_cookie(FLOW_COOKIE, path="/bind")
    resp.set_cookie(
        SESSION_COOKIE,
        session,
        max_age=deps.settings.session_ttl_s,
        httponly=True,
        samesite="strict",  # every state-changing form is same-site; Strict + a CSRF token
        secure=deps.settings.secure_cookies,
    )
    return resp


# Registered after /bind/callback so that "callback" is never taken for a token.
router.add_api_route("/bind/{token}", bind_page, methods=["GET"], response_class=HTMLResponse)
