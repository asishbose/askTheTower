"""`/openapi.json` vs the vendored specs: the only differences are the `/oauth2` and `/_admin`
additions and the two documented load-time patches; routers register exactly the spec's paths."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from mock_carrier.specs import SPEC_FILES, load_spec_file

pytestmark = pytest.mark.integration

SPECS = Path(__file__).resolve().parents[3] / "specs" / "camara"


async def test_merged_paths_are_spec_paths_plus_additions(client: httpx.AsyncClient) -> None:
    doc = (await client.get("/openapi.json")).json()
    expected = set()
    for filename in SPEC_FILES.values():
        spec = load_spec_file(SPECS / filename)
        base = spec["servers"][0]["url"].replace("{apiRoot}", "")
        expected |= {base + p for p in spec["paths"]}
    served = set(doc["paths"])
    extra = served - expected
    assert expected <= served
    assert extra and all(p.startswith(("/oauth2/", "/_admin/")) for p in extra), extra


@pytest.mark.parametrize("api", sorted(SPEC_FILES))
async def test_per_api_document_is_the_vendored_file_plus_two_patches(
    client: httpx.AsyncClient, api: str
) -> None:
    served = (await client.get(f"/openapi/{api}.json")).json()
    vendored = load_spec_file(SPECS / SPEC_FILES[api])
    # undo the two documented patches; what remains must be byte-for-byte the file's content
    assert served["servers"][0]["variables"]["apiRoot"]["default"] == "http://mock.test"
    assert served["components"]["securitySchemes"]["openId"]["openIdConnectUrl"].endswith(
        "/oauth2/.well-known/openid-configuration"
    )
    served["servers"] = vendored["servers"]
    served["components"]["securitySchemes"] = vendored["components"]["securitySchemes"]
    assert served == vendored


async def test_merged_operations_match_spec_operation_ids(client: httpx.AsyncClient) -> None:
    doc = (await client.get("/openapi.json")).json()
    served_ids = {
        op["operationId"]
        for path, item in doc["paths"].items()
        if not path.startswith(("/oauth2/", "/_admin/"))
        for op in item.values()
        if isinstance(op, dict) and "operationId" in op
    }
    vendored_ids = set()
    for filename in SPEC_FILES.values():
        spec = load_spec_file(SPECS / filename)
        vendored_ids |= {
            op["operationId"]
            for item in spec["paths"].values()
            for op in item.values()
            if isinstance(op, dict) and "operationId" in op
        }
    assert served_ids == vendored_ids and len(vendored_ids) == 15


async def test_routes_match_the_spec(client: httpx.AsyncClient) -> None:
    app = client._transport.app  # type: ignore[attr-defined]
    routes = {(m, r.path) for r in app.routes if getattr(r, "methods", None) for m in r.methods}
    for op in app.state.rt.specs.operations():
        assert (op.method, op.full_path) in routes, op.label


async def test_healthz_and_docs(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert (await client.get("/docs")).status_code == 200


async def test_admin_absent_without_flag(make_client) -> None:  # type: ignore[no-untyped-def]
    async with make_client(admin=False) as c:
        assert (await c.get("/_admin/state")).status_code == 404
        doc = (await c.get("/openapi.json")).json()
        assert not any(p.startswith("/_admin") for p in doc["paths"])


async def test_ciba_absent_without_flag(make_client) -> None:  # type: ignore[no-untyped-def]
    async with make_client(ciba=False) as c:
        assert (await c.post("/oauth2/bc-authorize")).status_code == 404
