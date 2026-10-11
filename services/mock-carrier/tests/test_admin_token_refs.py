"""08 §3, doc 11 §10 G1 + G2: the admin token on the mutating `/_admin` routes, and lines addressed by `ref`
so a demo tool never sends or receives a number."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from mock_carrier.state import line_ref
from mock_carrier.testing import ASISH, MOM

from tests.privacy.patterns import phone_hits

pytestmark = pytest.mark.integration

TOKEN = "test-admin-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MUTATIONS: list[tuple[str, str, dict[str, object] | None]] = [
    ("POST", "/_admin/scenarios/load", {"name": "demo"}),
    ("POST", "/_admin/clock", {"advance_s": 60}),
    ("POST", f"/_admin/lines/{line_ref(ASISH)}/events", {"event": "cf_set"}),
    ("POST", "/_admin/faults", {"kind": "500", "n": 1}),
    ("DELETE", "/_admin/faults", None),
]
READS = ["/_admin/clock", "/_admin/state", "/_admin/state?view=refs", "/_admin/scenarios", "/_admin/sink"]


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
async def test_mutations_need_the_token_when_set(
    make_client: Callable[..., httpx.AsyncClient], method: str, path: str, body: dict[str, object] | None
) -> None:
    async with make_client(admin_token=TOKEN) as c:
        assert (await c.request(method, path, json=body)).status_code == 401
        wrong = {"Authorization": "Bearer nope"}
        assert (await c.request(method, path, json=body, headers=wrong)).status_code == 401
        assert (await c.request(method, path, json=body, headers=AUTH)).status_code == 200


@pytest.mark.parametrize("path", READS)
async def test_reads_stay_open(make_client: Callable[..., httpx.AsyncClient], path: str) -> None:
    async with make_client(admin_token=TOKEN) as c:
        assert (await c.get(path)).status_code == 200  # Tower and Alerts read the clock without a token


async def test_no_token_configured_leaves_mutations_open(client: httpx.AsyncClient) -> None:
    assert (await client.post("/_admin/clock", json={"advance_s": 1})).status_code == 200


async def test_state_by_refs_has_no_number(client: httpx.AsyncClient) -> None:
    r = await client.get("/_admin/state", params={"view": "refs"})
    assert r.status_code == 200
    body = r.json()
    assert phone_hits(json.dumps(body)) == []
    assert line_ref(ASISH) in body["lines"] and line_ref(MOM) in body["lines"]
    asish = body["lines"][line_ref(ASISH)]
    assert "msisdn" not in asish and "phone-asish" in asish["mobile_data_client_ids"]
    assert "sink_inbox" not in body and "deliveries" not in body


async def test_event_by_ref_answers_without_the_number(client: httpx.AsyncClient) -> None:
    r = await client.post(f"/_admin/lines/{line_ref(MOM)}/events", json={"event": "unreachable"})
    assert r.status_code == 200
    assert phone_hits(r.text) == []
    assert r.json()["line"]["reachable"] is False
    state = (await client.get("/_admin/state")).json()
    assert state["lines"][MOM]["reachable"] is False


async def test_event_by_number_still_works(client: httpx.AsyncClient) -> None:
    r = await client.post(f"/_admin/lines/{ASISH}/events", json={"event": "cf_set"})
    assert r.status_code == 200 and r.json()["line"]["msisdn"] == ASISH


async def test_unknown_ref_is_404(client: httpx.AsyncClient) -> None:
    r = await client.post("/_admin/lines/line:0000000000000000/events", json={"event": "cf_set"})
    assert r.status_code == 404
