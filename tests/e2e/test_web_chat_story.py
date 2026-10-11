"""The web chat story (09 §6.6, bind-and-alert-flows Flow 1) against the running local stack: the page's sign-in
stub → "Is my line OK?" → `NOT_BOUND` + a bind link under `BINDING_BASE_URL/bind/` → the one tap with the
simulated phone → ask again → `OK`. Compared to `golden/web-chat/web-chat.json` on tool calls and reason codes;
wording is never compared.

It drives the agent exactly as `services/web-chat/app.js` does: `GET /config.js` (the stub's config), then
`POST /invocations` with the local bearer and `X-Tower-User`. To start from "not connected", Asish's `Lines` row
is removed from DynamoDB Local first (the compose seed bound it); the bind in the story writes it back, and a
failure re-binds it, so the demo state is unchanged afterwards. ENV=local only: the simulation (`?as=`) and the
stub exist only there.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from tests.helpers.env import MockAdmin, Stack, Targets
from tests.helpers.patterns import phone_hits
from tests.helpers.transcripts import GOLDEN, compare, load

pytestmark = pytest.mark.e2e

WEB_CHAT = os.environ.get("WEB_CHAT_URL", "http://127.0.0.1:8083").rstrip("/")
ASISH = "user-asish"
QUESTION = "Is my line OK?"


@pytest.fixture(scope="module")
def chat(stack: Stack, env: Targets) -> Iterator[httpx.Client]:
    if env.env != "local":
        pytest.skip(f"ENV={env.env}: the sign-in stub and the mobile-data simulation exist only locally")
    client = httpx.Client(base_url=WEB_CHAT, timeout=30.0)
    try:
        if client.get("/healthz").status_code != 200:
            pytest.skip(f"web chat not answering at {WEB_CHAT}: run `make up`")
    except httpx.HTTPError:
        pytest.skip(f"web chat not answering at {WEB_CHAT}: run `make up`")
    yield client
    client.close()


def page_config(chat: httpx.Client) -> dict[str, str]:
    r = chat.get("/config.js")
    assert r.status_code == 200, r.text
    return dict(json.loads(r.text.split("=", 1)[1].strip().rstrip(";")))


def ask(chat: httpx.Client, cfg: dict[str, str], session: str) -> httpx.Response:
    return chat.post(
        cfg["AGENT_URL"],
        json={"input": QUESTION, "session_id": session},
        headers={"Authorization": f"Bearer {cfg['LOCAL_BEARER']}", "X-Tower-User": ASISH},
    )


def unbind_asish() -> int:
    """Remove Asish's Lines row(s) in DynamoDB Local: the 'not connected yet' starting point of Flow 1."""
    from tower_consent import list_lines, tables

    from tests.helpers.env import resolve

    endpoint = os.environ.get("DYNAMO_ENDPOINT") or "http://localhost:8000"
    env = {
        "TOWER_DYNAMODB_ENDPOINT": endpoint,
        "TOWER_TABLE_PREFIX": os.environ.get("TOWER_TABLE_PREFIX", ""),
        "AWS_REGION": os.environ.get("AWS_REGION", "us-east-1"),
    }
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "dynamodblocal")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "dynamodblocal")
    from tower_consent import Store

    assert resolve().env == "local"
    store = Store.from_env(env)
    lines = list_lines(store, ASISH)
    for line in lines:
        store.delete(tables.LINES, {"line_id": line.line_id})
    return len(lines)


def bind_with_simulated_phone(binding_url: str, link: str) -> httpx.Response:
    """The one tap from 'Asish's phone on mobile data' (`?as=phone-asish`, TOWER_ENV=local), as the seed does it."""
    path = urlsplit(link).path
    with httpx.Client(base_url=binding_url, timeout=30.0) as phone:
        assert phone.get(path, params={"as": "phone-asish"}).status_code == 200
        r = phone.post(f"{path}/verify", data={"as": "phone-asish"})
        assert r.status_code == 303, r.text[:300]
        target = urlsplit(r.headers["location"])
        return phone.get(target.path + (f"?{target.query}" if target.query else ""))


def rebind_asish(binding_url: str) -> None:
    with httpx.Client(base_url=binding_url, timeout=30.0) as page:
        r = page.post("/_admin/bind-tokens", json={"user_id": ASISH})
        r.raise_for_status()
        bind_with_simulated_phone(binding_url, r.json()["url"])


def turn(reply: dict[str, Any]) -> dict[str, Any]:
    calls = [{"name": c["name"]} for c in reply["tool_calls"]]  # the endpoint carries names, not args
    codes = reply["tool_calls"][-1]["reason_codes"] if reply["tool_calls"] else []
    return {"utterance": QUESTION, "tool_calls": calls, "reason_codes": codes}


def test_web_chat_story(chat: httpx.Client, env: Targets, mock_admin: MockAdmin) -> None:
    cfg = page_config(chat)
    assert cfg["COGNITO_DOMAIN"] == "" and cfg["LOCAL_BEARER"]  # the sign-in stub
    base = cfg["BINDING_BASE_URL"]
    assert base, "WEB_CHAT_BINDING_BASE_URL is not set on the web-chat service"
    session = f"e2e-web-chat-{uuid.uuid4()}"
    mock_admin.load_scenario("demo")  # the clock at 14:00Z: Asish's line is quiet once bound
    unbind_asish()
    bodies: list[str] = []
    try:
        r = ask(chat, cfg, session)
        assert r.status_code == 200, r.text
        bodies.append(r.text)
        first = r.json()
        step = first["next_step"]
        assert step and step["kind"] == "bind_line" and step["url"].startswith(base + "/bind/"), step

        connected = bind_with_simulated_phone(env.binding_url, step["url"])
        assert connected.status_code == 200 and "Line connected" in connected.text
        assert not phone_hits(connected.text)

        r = ask(chat, cfg, session)
        assert r.status_code == 200, r.text
        bodies.append(r.text)
        second = r.json()
        assert second["next_step"] is None
    except BaseException:
        rebind_asish(env.binding_url)  # leave the demo as the seed made it
        raise
    transcript = {"story": "web-chat", "steps": [turn(first), turn(second)]}
    assert compare(load(GOLDEN / "web-chat" / "web-chat.json"), transcript) == []
    for body in bodies:
        assert not phone_hits(body) and "facts" not in body


def test_no_bearer_is_401(chat: httpx.Client) -> None:
    r = chat.post("/invocations", json={"input": QUESTION, "session_id": f"e2e-web-chat-{uuid.uuid4()}"})
    assert (r.status_code, r.json()) == (401, {"error": "unauthorized"})
