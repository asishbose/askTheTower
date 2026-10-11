"""FastAPI app: the binding page. `create_app(deps)` in tests; `create_app_from_env()` in the container;
`handler` is the Lambda entry point (Mangum, API Gateway HTTP API)."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from binding_page.alerts import AlertsWatch, HttpAlerts, RecordingAlerts
from binding_page.deps import Deps
from binding_page.routes import admin, audit, bind, grants, watch_settings

# The AWS SDK's DEBUG output is a wire dump (request bodies, ciphertext, 10-digit CRC32 headers). Nothing at
# that level belongs in this service's logs, whatever LOG_LEVEL is (04 §7 privacy grep; 06 §11.5 criterion 8).
WIRE_LOGGERS = ("botocore", "boto3", "urllib3")


def pin_wire_loggers() -> None:
    for name in WIRE_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def create_app(deps: Deps, *, close_on_shutdown: bool = False) -> FastAPI:
    pin_wire_loggers()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if close_on_shutdown:
            await deps.carrier_http.aclose()
            aclose = getattr(deps.carrier, "aclose", None)
            if aclose is not None:
                await aclose()
            close = getattr(deps.alerts, "close", None)
            if close is not None:
                close()

    app = FastAPI(
        title="Ask the Tower — binding page",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.deps = deps

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "env": deps.settings.tower_env})

    app.include_router(bind.router)
    app.include_router(grants.router)
    app.include_router(watch_settings.router)
    app.include_router(audit.router)
    app.include_router(admin.router)
    return app


def deps_from_env(env: Mapping[str, str] | None = None) -> Deps:
    from tower_audit import signer_from_env
    from tower_consent import Store, crypto_from_env

    from binding_page.carrier import client_from_env
    from binding_page.config import Settings

    settings = Settings.from_env(env)
    hasher, cipher = crypto_from_env(env)
    config, carrier = client_from_env(env)
    store_env = dict(os.environ if env is None else env)
    if not store_env.get("TOWER_DYNAMODB_ENDPOINT") and store_env.get("DYNAMO_ENDPOINT"):
        store_env["TOWER_DYNAMODB_ENDPOINT"] = store_env["DYNAMO_ENDPOINT"]  # the compose/Makefile name
    alerts: AlertsWatch = (
        HttpAlerts(settings.alerts_internal_url, settings.alerts_internal_bearer)
        if settings.alerts_internal_url
        else RecordingAlerts()
    )
    return Deps(
        settings=settings,
        store=Store.from_env(store_env),
        hasher=hasher,
        cipher=cipher,
        carrier_config=config,
        carrier=carrier,
        audit_signer=signer_from_env(env),
        carrier_http=httpx.AsyncClient(timeout=5.0, follow_redirects=False),
        alerts=alerts,
    )


def create_app_from_env(env: Mapping[str, str] | None = None) -> FastAPI:
    return create_app(deps_from_env(env), close_on_shutdown=True)


_handler: Any = None


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda entry point (API Gateway HTTP API → Mangum → this app). Built on first invocation."""
    global _handler
    if _handler is None:
        from mangum import Mangum

        _handler = Mangum(create_app_from_env(), lifespan="off")
    result: dict[str, Any] = _handler(event, context)
    return result
