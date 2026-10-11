"""Mangum (API Gateway HTTP API v2 event) round-trip for GET /bind/{token} — the AWS hosting shape."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from mangum import Mangum

pytestmark = pytest.mark.integration


def _event(path: str, query: str = "") -> dict[str, Any]:
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": query,
        "headers": {"host": "bind.example.test", "user-agent": "phone", "accept": "text/html"},
        "requestContext": {
            "accountId": "anonymous",
            "apiId": "api",
            "domainName": "bind.example.test",
            "domainPrefix": "bind",
            "http": {
                "method": "GET",
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "192.0.2.10",
                "userAgent": "phone",
            },
            "requestId": "req",
            "routeKey": "$default",
            "stage": "$default",
            "time": "06/Oct/2026:14:30:00 +0000",
            "timeEpoch": 0,
        },
        "isBase64Encoded": False,
    }


def _body(resp: dict[str, Any]) -> str:
    raw = resp["body"]
    return base64.b64decode(raw).decode() if resp.get("isBase64Encoded") else raw


def test_get_bind_page_through_mangum(make_page: Any) -> None:
    page = make_page(tower_env="aws")
    handler = Mangum(page.app, lifespan="off")
    token = page.bind_token("user-asish")
    resp = handler(_event(f"/bind/{token}", "as=phone-asish"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert "Turn Wi-Fi off" in body and f'action="/bind/{token}/verify"' in body
    assert "phone-asish" not in body  # AWS: the simulation parameter is ignored
    assert resp["headers"]["content-type"].startswith("text/html")


def test_unknown_token_through_mangum(make_page: Any) -> None:
    handler = Mangum(make_page(tower_env="aws").app, lifespan="off")
    resp = handler(_event("/bind/NoSuchTokenNoSuchTokenNoSuchTok"), None)
    assert resp["statusCode"] == 404


def test_healthz_through_mangum(make_page: Any) -> None:
    handler = Mangum(make_page(tower_env="aws").app, lifespan="off")
    resp = handler(_event("/healthz"), None)
    assert resp["statusCode"] == 200 and '"env":"aws"' in _body(resp)
