"""Nightly, live: the carrier operations through the deployed AgentCore Gateway → Identity → mock on Fargate
(05 §7 conformance, testing-and-showcase §2.5). Skips without AWS credentials or a deployment.

Locally the same fixtures run DirectClient vs GatewayClient → FakeGateway (packages/camara-client). Here the
mock is internal (DirectClient cannot reach it from outside the VPC), so the Gateway path is compared with what
the scenario says: `scripts/aws_seed.py` loads scenarios/demo.yaml at the mock's clock, then this test reads
every service-level operation for both demo lines, checks the tool list against the specs, and checks the
error map on a number the mock does not know.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import yaml
from camara_client import CarrierConfig, CarrierError, GatewayClient, LineRef, make_client, specs
from tower_policy import ReasonCode

from tests.aws.conftest import ROOT

pytestmark = pytest.mark.nightly

SCENARIO = yaml.safe_load((ROOT / "scenarios" / "demo.yaml").read_text())
NUMBERS = list(SCENARIO["lines"])  # fiction: 555-01xx


def gateway_client(outputs: dict[str, Any], profile: str = "proactive") -> GatewayClient:
    env = {
        "CARRIER_CLIENT": "gateway",
        "CARRIER_BACKEND": str(outputs["carrier_backend"]),
        "CARRIER_BASE_URL": str(outputs["carrier_base_url"]),
        "CARRIER_GATEWAY_URL": str(outputs["gateway_url"]),
        "CARRIER_GATEWAY_TOOLS": "discover",
        "CARRIER_GATEWAY_AUTH": "sigv4",
        "CARRIER_GATEWAY_REGION": str(outputs["region"]),
        "CARRIER_PROFILE": profile,  # proactive: 5 s budget — this test checks results, not latency
    }
    client = make_client(CarrierConfig.from_env(env), env=env)
    assert isinstance(client, GatewayClient)
    return client


async def test_gateway_lists_one_tool_per_operation(aws_outputs: dict[str, Any]) -> None:
    client = gateway_client(aws_outputs)
    assert await client.check_tools() == []
    assert set(client.tool_names) == {op.key for op in specs.operations()}
    await client.aclose()


async def test_service_operations_match_the_scenario(aws_outputs: dict[str, Any]) -> None:
    if aws_outputs.get("carrier_backend") != "mock":
        pytest.skip("scenario assertions need carrier_backend = mock")
    client = gateway_client(aws_outputs)
    for number in NUMBERS:
        line = LineRef("ln_conformance", number)
        expected = SCENARIO["lines"][number]
        swapped = (await client.sim_swap_check(line, 72)).swapped
        when = await client.sim_swap_date(line)
        assert when is not None and when.tzinfo is not None
        clock = datetime.fromisoformat(SCENARIO["clock"].replace("Z", "+00:00"))
        changed = datetime.fromisoformat(str(expected["sim_change_at"]).replace("Z", "+00:00"))
        assert swapped == (clock - changed <= timedelta(hours=72))
        cf = await client.call_forwarding(line)
        assert cf.status == expected["call_forwarding"]
        reach = await client.reachability(line)
        assert reach.reachable is bool(expected["reachable"])
    await client.aclose()


async def test_unknown_number_maps_to_not_bound(aws_outputs: dict[str, Any]) -> None:
    client = gateway_client(aws_outputs)
    with pytest.raises(CarrierError) as exc:
        await client.sim_swap_check(LineRef("ln_unknown", "+16135550199"), 72)
    assert exc.value.reason_code in (ReasonCode.NOT_BOUND, ReasonCode.CARRIER_ERROR)
    await client.aclose()


async def test_request_budget_through_gateway(aws_outputs: dict[str, Any]) -> None:
    """The 300 ms request profile through Gateway after warm-up (the number p95 is gated on lives in
    artifacts/latency-aws.md; this only proves one warm call fits)."""
    client = gateway_client(aws_outputs, profile="request")
    await client.check_tools()
    started = datetime.now(UTC)
    await client.sim_swap_check(LineRef("ln_conformance", NUMBERS[0]), 72)
    assert (datetime.now(UTC) - started).total_seconds() < 0.3
    await client.aclose()
