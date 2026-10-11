"""Prompt 13's live wiring of `GatewayClient`, proven offline:

- tool names resolved from Gateway's own `tools/list` (`resolve_tool_names`, `CARRIER_GATEWAY_TOOLS=discover`),
  including AgentCore's `<target>___<operationId>` naming;
- a persistent MCP session per process (`CARRIER_GATEWAY_SESSION=persistent`);
- SigV4 inbound auth (`camara_client.aws.SigV4Auth`), checked against botocore's own signer;
- the configuration path from the environment (`CarrierConfig.from_env`, `make_client`).

The live Gateway itself is exercised by tests/aws (nightly, needs AWS credentials).
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx2
import pytest
from botocore.auth import SigV4Auth as BotoSigV4
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from camara_client import BreakerRegistry, CarrierConfig, GatewayClient, LineRef, make_client, specs
from camara_client.aws import SigV4Auth, gateway_auth_from_env
from camara_client.gateway import AGENTCORE_DELIMITER, conventional_tools, resolve_tool_names
from camara_client.testing import FakeGateway, mock_config
from mock_carrier.testing import ASISH, BASE

OPS = [op for op in specs.operations()]


def agentcore_name(api: str, op: str) -> str:
    return f"{api}{AGENTCORE_DELIMITER}{op}"


# --- name resolution ----------------------------------------------------------------------------------------


@pytest.mark.unit
def test_resolves_agentcore_names() -> None:
    listed = [agentcore_name(op.api, op.operation_id) for op in OPS] + ["x_amz_bedrock_agentcore_search"]
    names = resolve_tool_names(listed)
    assert names["sim-swap/checkSimSwap"] == "sim-swap___checkSimSwap"
    assert set(names) == set(conventional_tools())


@pytest.mark.unit
def test_resolves_the_convention_and_a_renamed_target() -> None:
    assert resolve_tool_names(conventional_tools().values()) == conventional_tools()
    renamed = [f"camara-{op.api}___{op.operation_id}" for op in OPS]
    names = resolve_tool_names(renamed)
    assert names["call-forwarding-signal/retrieveCallForwarding"] == (
        "camara-call-forwarding-signal___retrieveCallForwarding"
    )


@pytest.mark.unit
def test_missing_or_ambiguous_tools_are_named() -> None:
    listed = [agentcore_name(op.api, op.operation_id) for op in OPS if op.operation_id != "checkSimSwap"]
    with pytest.raises(ValueError, match="sim-swap/checkSimSwap"):
        resolve_tool_names(listed)
    dup = [f"a___{op.operation_id}" for op in OPS] + ["b___checkSimSwap"]
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_tool_names(dup)


# --- discover + persistent session through the in-process Gateway --------------------------------------------


class CountingFactory:
    def __init__(self, fake: FakeGateway) -> None:
        self.fake = fake
        self.made = 0

    def __call__(self) -> Any:
        self.made += 1
        return self.fake.client()


@pytest.mark.integration
async def test_discover_mode_calls_agentcore_named_tools(
    mock_transport: Any, breakers: BreakerRegistry
) -> None:
    fake = FakeGateway(base_url=BASE, transport=mock_transport, tool_name=agentcore_name)
    config = mock_config(BASE, client="gateway").model_copy(update={"gateway_tools_file": "discover"})
    client = GatewayClient(config, client_factory=fake.client, breakers=breakers)
    assert client.tool_names == {}
    assert await client.check_tools() == []
    assert client.tool_names["sim-swap/checkSimSwap"] == "sim-swap___checkSimSwap"
    assert (await client.sim_swap_check(LineRef("l", ASISH), 72)).swapped is False
    await fake.aclose()


@pytest.mark.integration
async def test_discover_on_first_call_without_warmup(mock_transport: Any, breakers: BreakerRegistry) -> None:
    fake = FakeGateway(base_url=BASE, transport=mock_transport, tool_name=agentcore_name)
    config = mock_config(BASE, client="gateway", profile="proactive").model_copy(
        update={"gateway_tools_file": "discover"}
    )
    client = GatewayClient(config, client_factory=fake.client, breakers=breakers)
    assert (await client.reachability(LineRef("l", ASISH))).reachable is True
    assert "device-reachability-status/getReachabilityStatus" in client.tool_names
    await fake.aclose()


@pytest.mark.integration
async def test_persistent_session_is_opened_once(mock_transport: Any, breakers: BreakerRegistry) -> None:
    fake = FakeGateway(base_url=BASE, transport=mock_transport)
    factory = CountingFactory(fake)
    config = mock_config(BASE, client="gateway").model_copy(update={"gateway_session": "persistent"})
    client = GatewayClient(config, client_factory=factory, breakers=breakers)
    assert await client.check_tools() == []
    line = LineRef("l", ASISH)
    for _ in range(3):
        assert (await client.sim_swap_check(line, 72)).swapped is False
    assert factory.made == 1  # warm-up opened it; three calls reused it
    await client.aclose()
    assert (await client.sim_swap_check(line, 72)).swapped is False  # closed: falls back to per-call
    assert factory.made == 2
    await client.aclose()
    await fake.aclose()


@pytest.mark.integration
async def test_per_call_sessions_by_default(mock_transport: Any, breakers: BreakerRegistry) -> None:
    fake = FakeGateway(base_url=BASE, transport=mock_transport)
    factory = CountingFactory(fake)
    client = GatewayClient(mock_config(BASE, client="gateway"), client_factory=factory, breakers=breakers)
    line = LineRef("l", ASISH)
    await client.sim_swap_check(line, 72)
    await client.sim_swap_check(line, 72)
    assert factory.made == 2
    await fake.aclose()


# --- SigV4 ----------------------------------------------------------------------------------------------------

CREDS = Credentials("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "session-token-example")


class Frozen:
    """Stands in for botocore's refreshable credentials."""

    def get_frozen_credentials(self) -> Any:
        return CREDS.get_frozen_credentials()


@pytest.mark.unit
def test_sigv4_matches_botocore() -> None:
    url = "https://gw-abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    auth = SigV4Auth("us-east-1", credentials=Frozen())
    request = httpx2.Request("POST", url, content=body, headers={"content-type": "application/json"})
    flow = auth.auth_flow(request)
    signed = next(flow)
    authz = signed.headers["authorization"]
    assert authz.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-east-1/bedrock-agentcore/aws4_request" in authz
    assert "SignedHeaders=content-type;host;x-amz-date;x-amz-security-token" in authz
    assert signed.headers["x-amz-security-token"] == "session-token-example"

    # Recompute with botocore directly, at the same timestamp: the signature must be identical.
    ref = AWSRequest(
        method="POST",
        url=url,
        data=body,
        headers={"host": request.url.netloc.decode(), "content-type": "application/json"},
    )
    ref.context["timestamp"] = signed.headers["x-amz-date"]
    BotoSigV4(CREDS.get_frozen_credentials(), "bedrock-agentcore", "us-east-1").add_auth(ref)
    assert ref.headers["Authorization"] == authz
    assert hashlib.sha256(body).hexdigest() not in authz  # payload hash is signed, never sent


@pytest.mark.unit
def test_sigv4_signs_get_without_body() -> None:
    auth = SigV4Auth("eu-west-1", credentials=Frozen())
    request = httpx2.Request("GET", "https://gw.example/mcp")
    signed = next(auth.auth_flow(request))
    assert "/eu-west-1/bedrock-agentcore/aws4_request" in signed.headers["authorization"]
    assert "content-type" not in signed.headers["authorization"].split("SignedHeaders=")[1].split(",")[0]


@pytest.mark.unit
def test_sigv4_needs_a_region_and_has_no_secret_in_repr() -> None:
    with pytest.raises(ValueError):
        SigV4Auth("")
    assert "wJalr" not in repr(SigV4Auth("us-east-1", credentials=Frozen()))


@pytest.mark.unit
def test_gateway_auth_from_env() -> None:
    assert gateway_auth_from_env({}) is None
    assert gateway_auth_from_env({"CARRIER_GATEWAY_AUTH": "none"}) is None
    auth = gateway_auth_from_env({"CARRIER_GATEWAY_AUTH": "sigv4", "AWS_REGION": "us-west-2"})
    assert isinstance(auth, SigV4Auth) and auth.region == "us-west-2"
    with pytest.raises(ValueError):
        gateway_auth_from_env({"CARRIER_GATEWAY_AUTH": "basic"})


# --- configuration from the environment (what Terraform sets) ---------------------------------------------------

AWS_ENV = {
    "CARRIER_CLIENT": "gateway",
    "CARRIER_BACKEND": "mock",
    "CARRIER_BASE_URL": "https://mock.carrier.example.org",
    "CARRIER_GATEWAY_URL": "https://gw-abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
    "CARRIER_GATEWAY_TOOLS": "discover",
    "CARRIER_GATEWAY_AUTH": "sigv4",
    "CARRIER_GATEWAY_REGION": "us-east-1",
    "CARRIER_GATEWAY_SESSION": "persistent",
}


@pytest.mark.unit
def test_config_from_terraform_env() -> None:
    config = CarrierConfig.from_env(AWS_ENV)
    assert config.client == "gateway"
    assert config.gateway_auth == "sigv4" and config.gateway_region == "us-east-1"
    assert config.gateway_session == "persistent" and config.gateway_tools_file == "discover"
    assert CarrierConfig.from_env({"AWS_REGION": "ca-central-1"}).gateway_region == "ca-central-1"
    assert CarrierConfig.from_env({}).gateway_auth == "none"


@pytest.mark.unit
def test_make_client_builds_a_signed_gateway_client() -> None:
    client = make_client(CarrierConfig.from_env(AWS_ENV), env=AWS_ENV)
    assert isinstance(client, GatewayClient)
    assert client.tool_names == {}  # discover: resolved at warm-up, no file in the image
    made = client._factory()
    transport = made.transport
    assert isinstance(getattr(transport, "auth", None), SigV4Auth)
