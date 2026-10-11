"""Lambda function URL in front of the web chat agent's AgentCore Runtime (09 §6.3).

The page (CloudFront origin) cannot call the Runtime's invocation URL directly: the browser needs CORS, which the
function URL's own `cors` block answers (preflight included). This handler forwards `POST` bodies to
`AGENT_INVOKE_URL` with exactly three request headers — `Authorization`, `Content-Type` and
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` — and returns the Runtime's status and body unchanged.

It holds no credential (the caller's Cognito token is the Runtime's JWT authorizer's business), logs nothing (no
header, no body), never retries, and answers 502 when the Runtime cannot be reached. Standard library only: it is
zipped by Terraform (`modules/web_chat`), not built into an image.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any

FORWARDED = ("authorization", "content-type", "x-amzn-bedrock-agentcore-runtime-session-id")
MAX_BODY = 16 * 1024  # the agent takes 500 characters of input; anything near this is not the page
TIMEOUT_S = 55.0  # under the function's 60 s timeout, so the page gets a 502 rather than a Lambda error


def _reply(status: int, body: bytes | str, content_type: str = "application/json") -> dict[str, Any]:
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    return {"statusCode": status, "headers": {"content-type": content_type}, "body": text}


def _error(status: int, code: str) -> dict[str, Any]:
    return _reply(status, json.dumps({"error": code}))


def forwarded_headers(event: dict[str, Any]) -> dict[str, str]:
    """The three headers the agent needs, and nothing else (no cookies, no X-Forwarded-*, no origin)."""
    incoming = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    return {name: incoming[name] for name in FORWARDED if name in incoming}


def request_body(event: dict[str, Any]) -> bytes:
    raw = event.get("body") or ""
    return base64.b64decode(raw) if event.get("isBase64Encoded") else str(raw).encode("utf-8")


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "")
    if method != "POST":
        return _error(405, "method_not_allowed")
    body = request_body(event)
    if len(body) > MAX_BODY:
        return _error(413, "too_large")
    target = os.environ["AGENT_INVOKE_URL"]
    if not target.startswith("https://"):
        return _error(500, "misconfigured")
    req = urllib.request.Request(target, data=body, headers=forwarded_headers(event), method="POST")  # noqa: S310 — https only, checked above
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 — https only, checked above
            return _reply(resp.status, resp.read(), resp.headers.get("content-type", "application/json"))
    except urllib.error.HTTPError as e:
        # the Runtime's own answer: 401 from the JWT authorizer, 4xx/5xx from the agent
        return _reply(e.code, e.read(), e.headers.get("content-type", "application/json"))
    except (urllib.error.URLError, TimeoutError, OSError):
        return _error(502, "agent_unreachable")
