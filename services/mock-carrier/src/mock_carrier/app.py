"""FastAPI application. `/openapi.json` is the six vendored specs merged (plus `/oauth2` and `/_admin`),
not FastAPI's generated document; `/openapi/<api>.json` serves each vendored spec with the two
load-time patches only; `/docs` is Swagger UI over the merged document."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from mock_carrier import errors, oauth
from mock_carrier.routers import (
    admin,
    call_forwarding,
    number_verification,
    reachability,
    reachability_subs,
    sim_swap,
    sim_swap_subs,
)
from mock_carrier.routers.common import correlator_of
from mock_carrier.runtime import Runtime
from mock_carrier.settings import Settings

_HTTP_CODES = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "INVALID_ARGUMENT",
    429: "TOO_MANY_REQUESTS",
}


def _nullable_31_to_30(node: Any) -> Any:
    """FastAPI emits OpenAPI 3.1 `anyOf: [X, {type: null}]`; the merged document is 3.0.3."""
    if isinstance(node, dict):
        any_of = node.get("anyOf")
        if isinstance(any_of, list) and {"type": "null"} in any_of:
            rest = [n for n in any_of if n != {"type": "null"}]
            out = {k: _nullable_31_to_30(v) for k, v in node.items() if k != "anyOf"}
            if len(rest) == 1:
                out.update(_nullable_31_to_30(rest[0]))
            else:
                out["anyOf"] = [_nullable_31_to_30(n) for n in rest]
            out["nullable"] = True
            return out
        return {k: _nullable_31_to_30(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_nullable_31_to_30(v) for v in node]
    return node


def create_app(
    settings: Settings | None = None, *, sink_transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    rt = Runtime(settings, sink_transport=sink_transport)
    app = FastAPI(
        title="Ask the Tower — mock carrier",
        version="fall25",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.rt = rt

    # --- errors: every non-2xx is a CAMARA envelope -----------------------------------------------
    @app.exception_handler(errors.CamaraError)
    async def camara_error(request: Request, exc: errors.CamaraError) -> JSONResponse:
        return errors.envelope(exc, correlator=correlator_of(request))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        code = _HTTP_CODES.get(exc.status_code, "INTERNAL" if exc.status_code >= 500 else "INVALID_ARGUMENT")
        message = exc.detail if isinstance(exc.detail, str) else code
        resp = errors.envelope_for(exc.status_code, code, message, correlator=correlator_of(request))
        for k, v in (exc.headers or {}).items():
            resp.headers[k] = v
        if exc.status_code == 405:
            # Starlette lists only the first matching route's methods; list every method the spec has
            allowed = rt.specs.methods_for(request.url.path)
            if allowed:
                resp.headers["Allow"] = ", ".join(allowed)
        return resp

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = "/".join(str(p) for p in first.get("loc", ()) if p != "body") or "body"
        return errors.envelope_for(400, "INVALID_ARGUMENT", f"{where}: {first.get('msg', 'invalid')}"[:300])

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        return errors.envelope_for(
            500, "INTERNAL", "Unknown server error.", correlator=correlator_of(request)
        )

    # --- routes -----------------------------------------------------------------------------------
    for module in (
        sim_swap,
        sim_swap_subs,
        call_forwarding,
        number_verification,
        reachability,
        reachability_subs,
    ):
        module.mount(app, rt)
    app.include_router(oauth.router(rt))
    if settings.admin:
        app.include_router(admin.router(rt))

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "scenario": rt.state.scenario, "apis": rt.specs.versions()}

    def merged() -> dict[str, Any]:
        own = get_openapi(title=app.title, version=app.version, routes=app.routes)
        own = _nullable_31_to_30(own)
        return rt.specs.merged_document(
            extra_paths=own.get("paths", {}), extra_components=own.get("components", {})
        )

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi() -> JSONResponse:
        return JSONResponse(merged())

    @app.get("/openapi/{api}.json", include_in_schema=False)
    async def openapi_api(api: str) -> JSONResponse:
        if api not in rt.specs.specs:
            raise errors.NotFound(f"no vendored spec named {api!r}")
        return JSONResponse(rt.specs.document(api))

    @app.get("/docs", include_in_schema=False)
    async def docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/openapi.json", title="mock carrier — Swagger UI")

    return app
