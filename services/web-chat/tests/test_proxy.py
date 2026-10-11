"""09 §6.3 / §6.6: the Lambda URL proxy forwards exactly three headers to the agent runtime, passes the
Runtime's status through, answers 502 when it cannot reach it, and logs nothing."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import logging
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit

TARGET = (
    "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aagent/invocations?qualifier=DEFAULT"
)
SESSION = "web-chat-session-abcdef-0123-4567-89ab-cdef"


def load_handler() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "proxy" / "handler.py"
    spec = importlib.util.spec_from_file_location("web_chat_proxy_handler", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


handler = load_handler()


def event(method: str = "POST", body: str = '{"input":"Is my line OK?"}', **headers: str) -> dict[str, Any]:
    base = {
        "authorization": "Bearer tok",
        "content-type": "application/json",
        "x-amzn-bedrock-agentcore-runtime-session-id": SESSION,
        "cookie": "a=b",
        "origin": "https://page.example",
        "x-forwarded-for": "198.51.100.7",
        "x-tower-user": "user-asish",
        "host": "abc.lambda-url.us-east-1.on.aws",
    }
    return {
        "requestContext": {"http": {"method": method}},
        "headers": {**base, **headers},
        "body": body,
        "isBase64Encoded": False,
    }


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self._body = status, body
        self.headers = {"content-type": "application/json"}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *a: Any) -> None:
        return None


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[urllib.request.Request]:
    monkeypatch.setenv("AGENT_INVOKE_URL", TARGET)
    calls: list[urllib.request.Request] = []

    def urlopen(req: urllib.request.Request, timeout: float) -> FakeResponse:
        calls.append(req)
        return FakeResponse(200, b'{"text":"ok","next_step":null,"tool_calls":[]}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return calls


def test_only_three_headers_are_forwarded(sent: list[urllib.request.Request]) -> None:
    r = handler.handler(event())
    assert r["statusCode"] == 200 and json.loads(r["body"])["text"] == "ok"
    (req,) = sent
    assert req.full_url == TARGET and req.get_method() == "POST"
    assert {k.lower(): v for k, v in req.header_items()} == {
        "authorization": "Bearer tok",
        "content-type": "application/json",
        "x-amzn-bedrock-agentcore-runtime-session-id": SESSION,
    }
    assert req.data == b'{"input":"Is my line OK?"}'


def test_header_names_are_matched_case_insensitively(sent: list[urllib.request.Request]) -> None:
    ev = event()
    ev["headers"] = {"Authorization": "Bearer tok", "Content-Type": "application/json", "Cookie": "x"}
    handler.handler(ev)
    assert {k.lower() for k, _ in sent[0].header_items()} == {"authorization", "content-type"}


def test_base64_body_is_decoded(sent: list[urllib.request.Request]) -> None:
    ev = event(body=base64.b64encode(b'{"input":"hi"}').decode())
    ev["isBase64Encoded"] = True
    handler.handler(ev)
    assert sent[0].data == b'{"input":"hi"}'


def test_only_post_and_small_bodies(sent: list[urllib.request.Request]) -> None:
    assert handler.handler(event(method="GET"))["statusCode"] == 405
    assert handler.handler(event(body="x" * (handler.MAX_BODY + 1)))["statusCode"] == 413
    assert sent == []


def test_runtime_status_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_INVOKE_URL", TARGET)

    def refused(req: urllib.request.Request, timeout: float) -> Any:
        hdrs = Message()
        hdrs["content-type"] = "application/json"
        raise urllib.error.HTTPError(TARGET, 401, "Unauthorized", hdrs, io.BytesIO(b'{"message":"no"}'))

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    r = handler.handler(event())
    assert (r["statusCode"], r["body"]) == (401, '{"message":"no"}')


def test_unreachable_runtime_is_502_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_INVOKE_URL", TARGET)
    calls: list[int] = []

    def down(req: urllib.request.Request, timeout: float) -> Any:
        calls.append(1)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    r = handler.handler(event())
    assert (r["statusCode"], json.loads(r["body"])) == (502, {"error": "agent_unreachable"})
    assert calls == [1]


def test_plain_http_target_is_refused(monkeypatch: pytest.MonkeyPatch, sent: list[Any]) -> None:
    monkeypatch.setenv("AGENT_INVOKE_URL", "http://agent.internal/invocations")
    assert handler.handler(event())["statusCode"] == 500 and sent == []


def test_nothing_is_logged_or_printed(
    sent: list[Any], caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    handler.handler(event())
    out = capsys.readouterr()
    assert caplog.records == [] and out.out == "" and out.err == ""
    source = (Path(handler.__file__)).read_text(encoding="utf-8")
    assert "import logging" not in source and "print(" not in source
