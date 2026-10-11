"""Routes (doc 11 §2): the page, `/healthz`, `/events` (SSE), `/say`, `/macro/{name}`, `/carrier/*`, `/binding/*`.

Guards on every route but `/healthz`:
- `DEMO_UI_TOKEN` when set (decision 7): `?token=` once (sets an HttpOnly, SameSite=Strict cookie) or a bearer.
- POSTs need the `HX-Request` header HTMX sends. A browser cannot add it cross-site without a CORS preflight the
  UI never grants, so another web page cannot drive the UI on 127.0.0.1 (it can revoke grants).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib import resources
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from ref_client.mcp_client import TowerError

from demo_ui import views
from demo_ui.clients import FAULT_KINDS, LINE_EVENTS, MockAdmin, Unavailable
from demo_ui.deps import Deps
from demo_ui.feed import Feed, Frame
from demo_ui.runner import MACROS, Busy

log = logging.getLogger("demo_ui")

COOKIE = "demo_ui_token"
KEEPALIVE_S = 15.0
WIRE_LOGGERS = ("botocore", "boto3", "urllib3", "httpx", "httpcore", "mcp", "fastmcp")


def pin_wire_loggers() -> None:
    """Wire-level loggers stay at WARNING: their DEBUG dumps carry headers and bodies (python skill gotcha)."""
    for name in WIRE_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def status(text: str, level: str = "info") -> HTMLResponse:
    return HTMLResponse(views.render("status.html", text=text, level=level))


async def sse_frames(feed: Feed, keepalive_s: float = KEEPALIVE_S) -> AsyncIterator[str]:
    q = feed.subscribe()
    try:
        while True:
            try:
                frame: Frame = await asyncio.wait_for(q.get(), timeout=keepalive_s)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield frame.sse()
    finally:
        feed.unsubscribe(q)


def create_app(deps: Deps) -> FastAPI:
    pin_wire_loggers()
    s = deps.settings
    feed = deps.feed

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(feed.run(), name="feed")
        log.info("demo-ui up mode=%s env=%s carrier=%s", deps.mode, s.env, s.carrier_enabled)
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await deps.aclose()

    app = FastAPI(
        title="Ask the Tower — demo UI", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    static = resources.files("demo_ui") / "static"
    app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.url.path == "/healthz":
            return await call_next(request)
        via_query = False
        if s.ui_token:
            given = (
                request.cookies.get(COOKIE)
                or request.headers.get("authorization", "").removeprefix("Bearer ")
                or request.query_params.get("token", "")
            )
            if not hmac.compare_digest(given.encode(), s.ui_token.encode()):
                return Response("token required (DEMO_UI_TOKEN)", status_code=401)
            via_query = request.query_params.get("token") is not None
        if request.method == "POST" and request.headers.get("hx-request") != "true":
            return Response("POST needs the HX-Request header", status_code=403)
        response = await call_next(request)
        if via_query and s.ui_token:
            response.set_cookie(COOKIE, s.ui_token, httponly=True, samesite="strict")
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "mode": deps.mode,
            "env": s.env,
            "panes": {
                "conversation": True,
                "carrier": s.carrier_enabled,
                "feed_sms": s.sms_enabled,
                "binding_admin": s.binding_admin_enabled,
            },
            "macro_running": deps.runner.busy,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((static / "index.html").read_text(encoding="utf-8"))

    @app.get("/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(
            sse_frames(feed),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/fragment/pill", response_class=HTMLResponse)
    async def pill() -> HTMLResponse:
        return HTMLResponse(views.render("pill.html", mode=deps.mode, env=s.env))

    @app.get("/fragment/conversation-controls", response_class=HTMLResponse)
    async def conversation_controls() -> HTMLResponse:
        return HTMLResponse(
            views.render(
                "conversation_controls.html",
                macros=MACROS,
                captions=views.CAPTIONS,
                carrier=s.carrier_enabled,
                off_reason=s.carrier_off_reason,
                personas=s.personas,
                free_text=deps.mode == "bedrock",
            )
        )

    @app.get("/fragment/carrier-controls", response_class=HTMLResponse)
    async def carrier_controls() -> HTMLResponse:
        return HTMLResponse(
            views.render(
                "carrier_controls.html",
                enabled=s.carrier_enabled,
                reason=s.carrier_off_reason,
                events=LINE_EVENTS,
                faults=FAULT_KINDS,
            )
        )

    @app.post("/say", response_class=HTMLResponse)
    async def say(who: Annotated[str, Form()], text: Annotated[str, Form(max_length=200)]) -> HTMLResponse:
        if deps.mode != "bedrock":
            return status("scripted agent: free text is off (no Bedrock); use the macros", "fail")
        if who not in s.personas:
            raise HTTPException(status_code=400, detail="unknown persona")
        if deps.runner.busy:
            return status("a macro is running; wait for it to finish", "fail")
        try:
            turn = await deps.say(who, text)
        except TowerError as e:
            return status(str(e), "fail")
        feed.publish("turn", views.say_fragment(who, turn, agent=deps.mode))
        url = ((turn.result or {}).get("next_step") or {}).get("url")
        if url:
            feed.publish("qr", views.bind_fragment(views.with_phone(str(url), who, s.local), who))
        feed.poke()
        return status(f"{who}: answered")

    @app.post("/macro/{name}", response_class=HTMLResponse)
    async def macro(name: str) -> HTMLResponse:
        if not s.carrier_enabled:
            return status(s.carrier_off_reason, "fail")
        try:
            deps.runner.start(name)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such macro") from None
        except Busy:
            return HTMLResponse(
                views.render("status.html", text="a macro is already running", level="fail"), status_code=409
            )
        return status(f"{name}: started")

    def mock() -> MockAdmin:
        if deps.mock is None:  # carrier_call checks first; this keeps the type narrow
            raise Unavailable(s.carrier_off_reason)
        return deps.mock

    async def carrier_call(what: str, make: Callable[[], Awaitable[Any]]) -> HTMLResponse:
        if deps.mock is None:
            return status(s.carrier_off_reason, "fail")
        if deps.runner.busy:
            return status("a macro is running; the carrier controls wait for it", "fail")
        try:
            result = await make()
        except Unavailable as e:
            return status(str(e), "fail")
        feed.poke()
        return status(f"{what}{f' (fired: {", ".join(result)})' if result else ''}")

    @app.post("/carrier/reset", response_class=HTMLResponse)
    async def carrier_reset() -> HTMLResponse:
        return await carrier_call("Reset done (make seed)", deps.runner.reset)

    @app.post("/carrier/advance", response_class=HTMLResponse)
    async def advance(minutes: Annotated[int, Form(ge=1, le=240)]) -> HTMLResponse:
        return await carrier_call(f"clock +{minutes} min", lambda: mock().advance(minutes))

    @app.post("/carrier/fire", response_class=HTMLResponse)
    async def fire(
        who: Annotated[Literal["asish", "mom"], Form()],
        event: Annotated[Literal["sim_swap", "cf_set", "cf_clear", "unreachable", "reachable"], Form()],
    ) -> HTMLResponse:
        return await carrier_call(f"{event} on {who}'s line", lambda: mock().fire(who, event))

    @app.post("/carrier/fault", response_class=HTMLResponse)
    async def fault(
        kind: Annotated[Literal["timeout", "500", "429"], Form()], n: Annotated[int, Form(ge=1, le=10)] = 1
    ) -> HTMLResponse:
        return await carrier_call(f"fault {kind} ×{n}", lambda: mock().fault(kind, n))

    @app.post("/carrier/faults/clear", response_class=HTMLResponse)
    async def clear_faults() -> HTMLResponse:
        return await carrier_call("faults cleared", lambda: mock().clear_faults())

    @app.post("/binding/bind-link", response_class=HTMLResponse)
    async def bind_link(who: Annotated[Literal["asish", "mom"], Form()]) -> HTMLResponse:
        if deps.binding is None:
            return status("binding admin is local-only: the QR code comes from Tower's next_step", "fail")
        try:
            url = await deps.binding.bind_url(f"user-{who}")
        except Unavailable as e:
            return status(str(e), "fail")
        return HTMLResponse(views.bind_fragment(views.with_phone(url, who, s.local), who))

    @app.post("/binding/grant", response_class=HTMLResponse)
    async def grant(action: Annotated[Literal["grant", "revoke"], Form()]) -> HTMLResponse:
        if deps.binding is None:
            return status("grants and revokes happen on the resident's own phone at /me", "fail")
        try:
            await deps.binding.mom_grant(action)
        except Unavailable as e:
            return status(str(e), "fail")
        feed.poke()
        return status(f"Mom: {action} Asish's watch")

    app.state.deps = deps
    return app
