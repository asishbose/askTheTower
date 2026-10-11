"""09 §6.2 / §6.6: the reference client's HTTP app behind the web chat page.

The agent refuses an anonymous request before any model or Tower call; forwards the caller's bearer to Tower byte
for byte; copies `next_step` from the tool result, never from model text; drops a bind link outside the binding
page; maps Tower and Bedrock failures; and never puts a number, the input or the token in a response or a log.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from botocore.exceptions import ClientError
from ref_client.agent import Agent, BedrockAgent, ScriptedAgent
from ref_client.http import (
    HttpSettings,
    create_app,
    pick_next_step,
    pin_wire_loggers,
    tower_agents,
    vet_next_step,
)
from ref_client.mcp_client import TowerConfig, TowerError
from ref_client.transcript import ToolCall

from tests.privacy.patterns import AWS_KEY_ID, BEARER, HEALTH_WORDS, phone_hits

from .fakes import FakeBedrock, FakeTower

pytestmark = pytest.mark.integration

AGENT = "http://agent.test"
BIND = "http://bind.test"  # the in-process Tower's binding_base_url (conftest)
SESSION = "web-chat-session-abcdef-0123-4567-89ab-cdef"  # 33..128 of [A-Za-z0-9-]
LOCAL_BEARER = "local-dev-tower-bearer"  # conftest's Tower bearer
TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJl"  # JWT-shaped, not a real token
NUMBER = "".join(["4"] * 11)  # a phone-number-shaped run, built at runtime so no file holds one


def body(text: str = "Is my line OK?", session: str = SESSION) -> dict[str, str]:
    return {"input": text, "session_id": session}


def bind_result(url: str) -> dict[str, Any]:
    return {
        "summary": "Your line isn't connected yet.",
        "facts": {},
        "reason_codes": ["NOT_BOUND"],
        "next_step": {"kind": "bind_line", "url": url},
        "checked_at": "2026-10-05T14:00:00Z",
    }


class CountingSessions:
    """`AgentSessions` over a fake Tower; counts how many agents were opened (≙ Tower connections)."""

    def __init__(self, tower: FakeTower, bedrock: FakeBedrock | None = None) -> None:
        self.tower, self.bedrock = tower, bedrock
        self.opened: list[TowerConfig] = []

    def __call__(self, cfg: TowerConfig) -> Any:
        @asynccontextmanager
        async def session() -> AsyncIterator[Agent]:
            self.opened.append(cfg)
            if self.bedrock is not None:
                yield BedrockAgent(self.tower, client=self.bedrock, system_prompt="test")
            else:
                yield ScriptedAgent(self.tower)

        return session()


def client_for(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=AGENT)


def settings(**kw: Any) -> HttpSettings:
    return HttpSettings(
        **{"tower_env": "local", "binding_base_url": BIND, "local_bearer": LOCAL_BEARER, **kw}
    )


PHRASES = {
    "is my line ok": ToolCall("line_is_ok", {"line": "self"}),
    "is mom's line ok": ToolCall("line_is_ok", {"line": "mom"}),
}


# --- rule 1: no bearer, no model call -------------------------------------------------------------------------
async def test_no_bearer_is_401_before_any_model_or_tower_call() -> None:
    bedrock = FakeBedrock([("tool", "line_is_ok", {"line": "self"}), ("text", "fine")])
    sessions = CountingSessions(FakeTower(bind_result(f"{BIND}/bind/x")), bedrock)
    async with client_for(create_app(settings(), sessions, phrases=PHRASES)) as c:
        for headers in (
            {},
            {"Authorization": ""},
            {"Authorization": "Basic dXNlcjpwYXNz"},
            {"Authorization": "Bearer"},
            {"Authorization": "Bearer two words"},
            {"Authorization": f"Bearer {TOKEN}\nX-Evil: 1"},
        ):
            r = await c.post("/invocations", json=body(), headers=headers)
            assert (r.status_code, r.json()) == (401, {"error": "unauthorized"}), headers
        r = await c.post("/invocations", content=b"not json")  # 401 wins over a bad body
        assert r.status_code == 401
    assert bedrock.requests == [] and sessions.opened == [] and sessions.tower.calls == []


# --- rule 2: body and numbers ---------------------------------------------------------------------------------
async def test_bad_body_is_422_and_a_number_never_reaches_the_model() -> None:
    bedrock = FakeBedrock([])
    sessions = CountingSessions(FakeTower(bind_result(f"{BIND}/bind/x")), bedrock)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    async with client_for(create_app(settings(), sessions, phrases=PHRASES)) as c:
        for bad in (
            {"input": "", "session_id": SESSION},
            {"input": "x" * 501, "session_id": SESSION},
            {"input": "hi", "session_id": "short"},
            {"input": "hi", "session_id": SESSION + "/"},
            {"input": "hi"},
            {"input": "hi", "session_id": SESSION, "extra": 1},
        ):
            r = await c.post("/invocations", json=bad, headers=auth)
            assert (r.status_code, r.json()) == (422, {"error": "invalid_request"}), bad
        for text in (f"is {NUMBER} ok", f"text +{NUMBER} for me"):
            r = await c.post("/invocations", json=body(text), headers=auth)
            assert (r.status_code, r.json()) == (422, {"error": "no_numbers"})
    assert bedrock.requests == [] and sessions.opened == []


# --- rule 3: the bearer reaches Tower unchanged (real in-process Tower) ---------------------------------------
class RecordingAsgi:
    """Records the headers of every HTTP request that reaches Tower's ASGI app."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.headers: list[dict[str, str]] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            self.headers.append({k.decode().lower(): v.decode() for k, v in scope["headers"]})
        await self.app(scope, receive, send)


def tower_factory(app: Any) -> Callable[..., Any]:
    import httpx2

    def factory(headers: Any = None, timeout: Any = None, auth: Any = None, **kw: Any) -> Any:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://tower.test",
            headers=headers,
            timeout=timeout if timeout is not None else 30.0,
            auth=auth,
            follow_redirects=True,
        )

    return factory


def real_tower(ref_stack: Any, **kw: Any) -> tuple[Any, RecordingAsgi]:
    rec = RecordingAsgi(ref_stack.tower_app)
    agents = tower_agents("scripted", httpx_client_factory=tower_factory(rec))
    app = create_app(settings(tower_url="http://tower.test/mcp", **kw), agents, phrases=PHRASES)
    return app, rec


async def test_bearer_is_forwarded_byte_for_byte_and_the_user_header_only_locally(ref_stack: Any) -> None:
    app, rec = real_tower(ref_stack)
    header = f"bearer {LOCAL_BEARER}"  # odd-cased scheme: forwarded as sent, not rebuilt
    async with client_for(app) as c:
        r = await c.post(
            "/invocations", json=body(), headers={"Authorization": header, "X-Tower-User": "user-asish"}
        )
    assert r.status_code == 200, r.text
    assert r.json()["tool_calls"] == [{"name": "line_is_ok", "reason_codes": ["OK"]}]
    assert r.json()["next_step"] is None
    assert rec.headers and all(h["authorization"] == header for h in rec.headers)
    assert all(h["x-tower-user"] == "user-asish" for h in rec.headers)

    app, rec = real_tower(ref_stack, tower_env="aws")
    async with client_for(app) as c:
        await c.post(
            "/invocations",
            json=body(),
            headers={"Authorization": f"Bearer {LOCAL_BEARER}", "X-Tower-User": "user-asish"},
        )
    assert rec.headers and all("x-tower-user" not in h for h in rec.headers)
    assert all(h["authorization"] == f"Bearer {LOCAL_BEARER}" for h in rec.headers)


async def test_tower_refusing_the_bearer_is_401(ref_stack: Any) -> None:
    app, _ = real_tower(ref_stack)
    async with client_for(app) as c:
        r = await c.post(
            "/invocations",
            json=body(),
            headers={"Authorization": "Bearer wrong", "X-Tower-User": "user-asish"},
        )
    assert (r.status_code, r.json()) == (401, {"error": "unauthorized"})


async def test_not_bound_link_is_tower_s_next_step_verbatim(ref_stack: Any) -> None:
    """A user with no line: NOT_BOUND, and the bind link is the one Tower minted, byte for byte."""
    app, _ = real_tower(ref_stack)
    async with client_for(app) as c:
        r = await c.post(
            "/invocations",
            json=body(),
            headers={"Authorization": f"Bearer {LOCAL_BEARER}", "X-Tower-User": "user-web-chat-new"},
        )
    assert r.status_code == 200, r.text
    reply = r.json()
    assert reply["tool_calls"] == [{"name": "line_is_ok", "reason_codes": ["NOT_BOUND"]}]
    step = reply["next_step"]
    assert step["kind"] == "bind_line" and step["url"].startswith(f"{BIND}/bind/")
    async with ref_stack.tower("user-web-chat-new") as tower:  # the same user asks Tower directly
        direct = await tower.call("line_is_ok", {"line": "self"})
    assert set(direct["next_step"]) == set(step)  # same envelope keys; the token is fresh per call


# --- rule 4: next_step is copied, never parsed from model text ------------------------------------------------
async def test_next_step_is_byte_identical_to_the_tool_result() -> None:
    result = bind_result(f"{BIND}/bind/tok-123")
    result["next_step"] = {
        "url": f"{BIND}/bind/tok-123",
        "kind": "bind_line",
        "extra": None,
    }  # key order kept
    sessions = CountingSessions(FakeTower(result))
    async with client_for(create_app(settings(), sessions, phrases=PHRASES)) as c:
        r = await c.post("/invocations", json=body(), headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert json.dumps(r.json()["next_step"]) == json.dumps(result["next_step"])
    assert json.dumps(result["next_step"], separators=(",", ":")) in r.text


async def test_a_url_in_model_text_never_becomes_a_next_step() -> None:
    bedrock = FakeBedrock([("text", f"Tap {BIND}/bind/made-up-by-the-model to connect your line.")])
    sessions = CountingSessions(FakeTower(bind_result(f"{BIND}/bind/x")), bedrock)
    async with client_for(create_app(settings(), sessions, phrases=PHRASES)) as c:
        r = await c.post("/invocations", json=body(), headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert r.json()["next_step"] is None and r.json()["tool_calls"] == []
    assert len(bedrock.requests) == 1


async def test_bedrock_path_takes_next_step_from_the_tool_result() -> None:
    url = f"{BIND}/bind/from-tower"
    bedrock = FakeBedrock([("tool", "line_is_ok", {"line": "self"}), ("text", f"Open {BIND}/bind/other")])
    sessions = CountingSessions(FakeTower(bind_result(url)), bedrock)
    async with client_for(create_app(settings(), sessions, phrases=PHRASES)) as c:
        r = await c.post("/invocations", json=body("anything"), headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.json()["next_step"] == {"kind": "bind_line", "url": url}
    assert r.json()["tool_calls"] == [{"name": "line_is_ok", "reason_codes": ["NOT_BOUND"]}]


async def test_tool_result_without_a_step_and_a_url_in_model_text_gives_null() -> None:
    """Acceptance (prompt 20): a model reply containing a URL never produces a next_step, even after a tool
    call whose result carries none."""
    quiet = {**bind_result(f"{BIND}/bind/x"), "reason_codes": ["OK"], "next_step": {"kind": "none"}}
    bedrock = FakeBedrock(
        [("tool", "line_is_ok", {"line": "self"}), ("text", f"All fine. Also see {BIND}/bind/model-made")]
    )
    sessions = CountingSessions(FakeTower(quiet), bedrock)
    async with client_for(create_app(settings(), sessions, phrases=PHRASES)) as c:
        r = await c.post("/invocations", json=body("anything"), headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert r.json()["next_step"] is None
    assert r.json()["tool_calls"] == [{"name": "line_is_ok", "reason_codes": ["OK"]}]


async def test_cognito_bearer_reaches_the_fake_tower_unchanged_on_aws() -> None:
    """Acceptance (prompt 20): with a bearer, the same bearer reaches the (fake) Tower unchanged; on AWS the
    caller's X-Tower-User never does (09 §6.2 rule 3)."""
    header = f"Bearer {TOKEN}"
    sessions = CountingSessions(FakeTower(bind_result(f"{BIND}/bind/x")))
    async with client_for(create_app(settings(tower_env="aws"), sessions, phrases=PHRASES)) as c:
        r = await c.post(
            "/invocations", json=body(), headers={"Authorization": header, "X-Tower-User": "user-mom"}
        )
    assert r.status_code == 200, r.text
    (cfg,) = sessions.opened
    assert cfg.authorization == header and cfg.user_id is None
    assert cfg.headers()["Authorization"] == header and "X-Tower-User" not in cfg.headers()
    assert sessions.tower.calls == [("line_is_ok", {"line": "self"})]


@pytest.mark.parametrize(
    ("results", "want"),
    [
        ([], None),
        ([{"next_step": {"kind": "none"}}], None),
        ([{"next_step": {"kind": "ask_consent"}}, {"next_step": {"kind": "none"}}], {"kind": "ask_consent"}),
        (
            [{"next_step": {"kind": "ask_consent"}}, {"next_step": {"kind": "bind_line", "url": "u"}}],
            {"kind": "bind_line", "url": "u"},
        ),
        ([{"reason_codes": ["OK"]}], None),
    ],
)
def test_pick_next_step_is_the_last_non_none(results: list[dict[str, Any]], want: Any) -> None:
    assert pick_next_step(results) == want


# --- rule 5: the /bind/ prefix ---------------------------------------------------------------------------------
async def test_foreign_bind_url_is_dropped_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="ref_client.http")
    for url in (
        "https://evil.example/bind/x",
        f"{BIND}/other/x",
        f"{BIND}.evil.example/bind/x",
        f"{BIND}/bindx",
    ):
        sessions = CountingSessions(FakeTower(bind_result(url)))
        async with client_for(create_app(settings(), sessions, phrases=PHRASES)) as c:
            r = await c.post("/invocations", json=body(), headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200 and r.json()["next_step"] is None, url
        assert r.json()["tool_calls"] == [{"name": "line_is_ok", "reason_codes": ["NOT_BOUND"]}]
    rejected = [rec for rec in caplog.records if "NEXT_STEP_REJECTED" in rec.getMessage()]
    assert len(rejected) == 4
    assert all("evil" not in rec.getMessage() and "/bind" not in rec.getMessage() for rec in rejected)


def test_vet_next_step() -> None:
    ok = {"kind": "bind_line", "url": f"{BIND}/bind/t"}
    assert vet_next_step(ok, BIND) == (ok, None)
    assert vet_next_step(ok, "") == (
        ok,
        None,
    )  # no base configured: Tower's link passes (rule 5 is conditional)
    assert vet_next_step({"kind": "bind_line"}, BIND) == (None, "url_outside_bind")
    assert vet_next_step({"kind": "ask_consent"}, BIND) == ({"kind": "ask_consent"}, None)
    call = {"kind": "call_carrier", "carrier_support_number": "611"}
    assert vet_next_step(call, BIND) == (call, None)
    long_number = {"kind": "call_carrier", "carrier_support_number": NUMBER}
    assert vet_next_step(long_number, BIND) == (None, "number")


# --- rule 7: failures ------------------------------------------------------------------------------------------
async def test_tower_unreachable_is_502_and_bedrock_down_is_503() -> None:
    def unreachable(cfg: TowerConfig) -> Any:
        @asynccontextmanager
        async def session() -> AsyncIterator[Agent]:
            raise TowerError("cannot connect")
            yield  # pragma: no cover

        return session()

    async with client_for(create_app(settings(), unreachable, phrases=PHRASES)) as c:
        r = await c.post("/invocations", json=body(), headers={"Authorization": f"Bearer {TOKEN}"})
    assert (r.status_code, r.json()) == (502, {"error": "tower_unavailable"})

    class DownBedrock(FakeBedrock):
        def converse(self, **kw: Any) -> dict[str, Any]:
            self.requests.append(kw)
            raise ClientError(
                {"Error": {"Code": "ServiceUnavailableException", "Message": "down"}}, "Converse"
            )

    sessions = CountingSessions(FakeTower(bind_result(f"{BIND}/bind/x")), DownBedrock([]))
    async with client_for(create_app(settings(), sessions, phrases=PHRASES)) as c:
        r = await c.post("/invocations", json=body(), headers={"Authorization": f"Bearer {TOKEN}"})
    assert (r.status_code, r.json()) == (503, {"error": "agent_unavailable"})


# --- the other routes ------------------------------------------------------------------------------------------
async def test_ping_healthz_config_and_page(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>web chat</title>", encoding="utf-8")
    sessions = CountingSessions(FakeTower(bind_result(f"{BIND}/bind/x")))
    async with client_for(create_app(settings(web_chat_dir=tmp_path), sessions, phrases=PHRASES)) as c:
        assert (await c.get("/ping")).json() == {"status": "Healthy"}
        assert (await c.get("/healthz")).json() == {"status": "ok"}
        cfg = await c.get("/config.js")
        assert cfg.status_code == 200 and cfg.headers["content-type"].startswith("text/javascript")
        values = json.loads(cfg.text.split("=", 1)[1].strip().rstrip(";"))
        assert values == {
            "COGNITO_DOMAIN": "",
            "CLIENT_ID": "",
            "REDIRECT_URI": "",
            "AGENT_URL": "/invocations",
            "BINDING_BASE_URL": BIND,
            "LOCAL_BEARER": LOCAL_BEARER,
        }
        page = await c.get("/")
        assert page.status_code == 200 and "web chat" in page.text
    async with client_for(create_app(settings(tower_env="aws"), sessions, phrases=PHRASES)) as c:
        assert (await c.get("/config.js")).status_code == 404  # never outside TOWER_ENV=local
        assert (await c.get("/")).status_code == 404  # no WEB_CHAT_DIR: AWS serves the page from S3


def test_settings_from_env() -> None:
    s = HttpSettings.from_env(
        {
            "TOWER_URL": "https://tower.example/mcp",
            "TOWER_ENV": "aws",
            "WEB_CHAT_BINDING_BASE_URL": "https://bind.example/",
            "REF_AGENT": "bedrock",
            "REF_CLIENT_HTTP_PORT": "9000",
        }
    )
    assert (s.tower_url, s.local, s.binding_base_url, s.agent, s.port) == (
        "https://tower.example/mcp",
        False,
        "https://bind.example",
        "bedrock",
        9000,
    )
    assert HttpSettings.from_env({}).port == 8080
    with pytest.raises(ValueError, match="REF_AGENT"):
        HttpSettings.from_env({"REF_AGENT": "gpt"})


# --- rule 8 + privacy: responses and logs ---------------------------------------------------------------------
async def test_privacy_sweep_over_responses_and_logs(
    ref_stack: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    pin_wire_loggers()  # what the composition root does; Tower's own logs stay at DEBUG here
    app, _ = real_tower(ref_stack)
    texts: list[str] = []
    async with client_for(app) as c:
        for user, text in (
            ("user-asish", "Is my line OK?"),
            ("user-asish", "Is Mom's line OK?"),
            ("user-mom", "Is my line OK?"),
            ("user-web-chat-new", "Is my line OK?"),
            ("user-asish", f"is {NUMBER} ok"),
            ("user-asish", "what's the weather"),
        ):
            r = await c.post(
                "/invocations",
                json=body(text),
                headers={"Authorization": f"Bearer {LOCAL_BEARER}", "X-Tower-User": user},
            )
            assert r.status_code in (200, 422)
            texts.append(r.text)
            assert "facts" not in r.text  # tool_calls carries names and reason codes only
    logs = "\n".join(rec.getMessage() for rec in caplog.records)
    for blob in [*texts, logs]:
        assert not phone_hits(blob), phone_hits(blob)
        assert not AWS_KEY_ID.search(blob) and not BEARER.search(blob)  # no key or token, anywhere
    for blob in texts:  # acceptance: no health word in any response
        assert not HEALTH_WORDS.search(blob), HEALTH_WORDS.findall(blob)
    ours = [rec.getMessage() for rec in caplog.records if rec.name == "ref_client.http"]
    assert len([m for m in ours if '"event": "invocation"' in m]) == 6
    for m in ours:
        assert LOCAL_BEARER not in m and "line OK" not in m and SESSION not in m and "/bind/" not in m
