"""The same spec-derived fixtures through `DirectClient` and through `GatewayClient` → FakeGateway
(an in-process FastMCP server that proxies to the carrier like AgentCore Gateway does). Both must
send the request the fixture expects and produce byte-identical outcomes; the outcomes must also
match what the spec says (happy bodies → results, error codes → the 05 §5 map)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from camara_client import BreakerRegistry, DirectClient, GatewayClient, LineRef, map_error, tools_manifest
from camara_client.gateway import load_tool_names
from camara_client.testing import (
    FakeGateway,
    FixtureCarrier,
    fixture_cases,
    mock_config,
    outcome_json,
    run_case,
)
from mock_carrier.testing import ASISH, BASE, MOM, REDIRECT

CASES = list(fixture_cases())
FIXTURE_BASE = "http://carrier.fixture"
DELETE_OPS = {
    "sim-swap-subscriptions/deleteSubscription",
    "device-reachability-status-subscriptions/deleteDeviceReachabilityStatusSubscription",
}


def _utc(value: str | None) -> str | None:
    return None if value is None else datetime.fromisoformat(value).astimezone(UTC).isoformat()


def expected_outcome(op: str, case: dict[str, Any]) -> dict[str, Any]:
    """What the spec says the client must return, written independently of the client code."""
    first, last = case["exchanges"][0]["response"], case["exchanges"][-1]["response"]
    status, body = last["status"], last.get("json")
    args = case["call"]["args"]
    if status >= 400:
        if op in DELETE_OPS and status in (404, 410):
            return {"result": None, "type": "NoneType"}
        code = body.get("code") if isinstance(body, dict) else None
        return {"error": map_error(status, code).as_dict()}
    if op == "sim-swap/checkSimSwap":
        return {"result": {"swapped": body["swapped"]}, "type": "SimSwapResult"}
    if op == "sim-swap/retrieveSimSwapDate":
        when = _utc(body["latestSimChange"])
        return {"result": when, "type": "datetime"} if when else {"result": None, "type": "NoneType"}
    if op == "call-forwarding-signal/retrieveCallForwarding":
        if first["status"] == 501:
            status_ = "unconditional" if body["active"] else "none"
        elif "unconditional" in body:
            status_ = "unconditional"
        elif any(x.startswith("conditional") for x in body):
            status_ = "conditional"
        else:
            status_ = "none"
        return {"result": {"status": status_}, "type": "CFResult"}
    if op == "number-verification/phoneNumberVerify":
        ok = body["devicePhoneNumberVerified"]
        return {
            "result": {"verified": ok, "e164": args["e164"] if ok else None},
            "type": "NumberVerifyResult",
        }
    if op == "number-verification/phoneNumberShare":
        return {"result": {"verified": True, "e164": body["devicePhoneNumber"]}, "type": "NumberVerifyResult"}
    if op == "device-reachability-status/getReachabilityStatus":
        reachable = body["reachable"]
        return {
            "result": {
                "reachable": reachable,
                "connectivity": sorted(set(body.get("connectivity", []))) if reachable else [],
                "last_status_time": _utc(body.get("lastStatusTime")).replace("+00:00", "Z")
                if body.get("lastStatusTime")
                else None,
            },
            "type": "ReachResult",
        }
    if op.endswith("Subscription") and "create" in op:
        return {"result": f"{op.split('/')[0]}/{body['id']}", "type": "str"}
    if op in DELETE_OPS:
        return {"result": None, "type": "NoneType"}
    raise AssertionError(f"no expectation for {op}")


def _clients(
    case: dict[str, Any],
) -> tuple[FixtureCarrier, DirectClient, FixtureCarrier, FakeGateway, GatewayClient]:
    client_id = "binding-page" if case["call"]["method"] == "number_verify" else "tower"
    d_carrier, g_carrier = FixtureCarrier(case), FixtureCarrier(case)
    direct = DirectClient(
        mock_config(FIXTURE_BASE, client_id=client_id),
        secret="fixture-secret",
        transport=d_carrier.transport,
        breakers=BreakerRegistry(),
    )
    fake = FakeGateway(base_url=FIXTURE_BASE, transport=g_carrier.transport, secret="fixture-secret")
    gateway = GatewayClient(
        mock_config(FIXTURE_BASE, client="gateway"), client_factory=fake.client, breakers=BreakerRegistry()
    )
    return d_carrier, direct, g_carrier, fake, gateway


@pytest.mark.integration
@pytest.mark.parametrize(("name", "case"), CASES, ids=[n for n, _ in CASES])
async def test_fixture_identical_through_both_clients(name: str, case: dict[str, Any]) -> None:
    op = name.split(" :: ")[0]
    d_carrier, direct, g_carrier, fake, gateway = _clients(case)
    try:
        via_direct = await run_case(direct, case)
        via_gateway = await run_case(gateway, case)
    finally:
        await direct.aclose()
        await fake.aclose()
    assert d_carrier.mismatches == [] and g_carrier.mismatches == []
    assert d_carrier.unconsumed == 0 and g_carrier.unconsumed == 0
    assert outcome_json(via_direct) == outcome_json(via_gateway)
    assert via_direct == expected_outcome(op, case)


@pytest.mark.unit
def test_fixture_counts() -> None:
    per_op: dict[str, int] = {}
    for name, case in CASES:
        per_op[name.split(" :: ")[0]] = per_op.get(name.split(" :: ")[0], 0) + 1
        assert case["exchanges"], name
    assert len(per_op) == 10
    assert all(n >= 8 for n in per_op.values()), per_op


@pytest.mark.unit
async def test_gateway_lists_one_tool_per_operation(fake_gateway: FakeGateway) -> None:
    async with fake_gateway.client() as c:
        names = sorted(t.name for t in await c.list_tools())
    assert names == sorted(tools_manifest()["tools"].values())
    assert "sim-swap__checkSimSwap" in names and len(names) == 15


@pytest.mark.unit
def test_tool_names_come_from_gateway_tools_json(tmp_path) -> None:  # type: ignore[no-untyped-def]
    renamed = {k: "carrier-target___" + k.split("/")[1] for k in tools_manifest()["tools"]}
    path = tmp_path / "gateway-tools.json"
    import json

    path.write_text(json.dumps(tools_manifest(renamed, gateway="gw-123")))
    assert load_tool_names(path) == renamed
    path.write_text(json.dumps({"tools": {"sim-swap/checkSimSwap": "x"}}))
    with pytest.raises(ValueError, match="no Gateway tool"):
        load_tool_names(path)


@pytest.mark.integration
async def test_renamed_tools_still_work(mock_transport, breakers) -> None:  # type: ignore[no-untyped-def]
    """A Gateway that names tools differently (e.g. `<target>___<operationId>`) needs only the map."""
    fake = FakeGateway(base_url=BASE, transport=mock_transport, tool_name=lambda api, op: f"carrier___{op}")
    names = {k: f"carrier___{k.split('/')[1]}" for k in tools_manifest()["tools"]}
    client = GatewayClient(
        mock_config(BASE, client="gateway"), client_factory=fake.client, tool_names=names, breakers=breakers
    )
    assert (await client.sim_swap_check(LineRef("l", ASISH), 72)).swapped is False
    await fake.aclose()


@pytest.mark.integration
async def test_live_mock_identical_through_both(
    direct: DirectClient, gateway: GatewayClient, fake_gateway: FakeGateway, mock
) -> None:
    line = LineRef("line-asish", ASISH)

    async def snapshot(client: Any) -> str:
        return outcome_json(
            {
                "swap": (await client.sim_swap_check(line, 72)).model_dump(mode="json"),
                "date": (await client.sim_swap_date(line)).isoformat(),
                "cf": (await client.call_forwarding(line)).model_dump(mode="json"),
                "reach": (await client.reachability(line)).model_dump(mode="json"),
            }
        )

    assert await snapshot(direct) == await snapshot(gateway)
    await mock.advance(20 * 60)  # SIM swap at +12, call forwarding at +20
    a, b = await snapshot(direct), await snapshot(gateway)
    assert a == b and '"swapped": true' in a and '"unconditional"' in a
    assert "sim-swap/checkSimSwap" in fake_gateway.calls


@pytest.mark.integration
async def test_number_verify_through_gateway(gateway: GatewayClient, mock) -> None:
    binding = DirectClient(mock_config(BASE, client_id="binding-page"), secret="unused")
    url = binding.authorization_url(REDIRECT, "s")
    code = await mock.auth_code(url, "phone-mom")
    result = await gateway.number_verify(code, redirect_uri=REDIRECT, e164=MOM)
    assert result.verified is True and result.e164 == MOM
    await binding.aclose()


@pytest.mark.integration
async def test_subscriptions_through_gateway(gateway: GatewayClient, mock) -> None:
    now = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
    sub = await gateway.subscribe(
        "sim-swap", LineRef("l", ASISH), "https://sink.mock.local/h/abc", timedelta(hours=2), now=now
    )
    await mock.event(ASISH, "sim_swap")
    assert len(await mock.sink()) == 1
    await gateway.unsubscribe(sub)
    await gateway.unsubscribe(sub)


@pytest.mark.unit
def test_gateway_needs_a_url() -> None:
    with pytest.raises(ValueError):
        mock_config(BASE, client="direct").model_copy(update={"client": "gateway"})
        GatewayClient(mock_config(BASE, client="direct"))
