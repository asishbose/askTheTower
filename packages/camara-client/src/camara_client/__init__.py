"""CarrierClient protocol with DirectClient and GatewayClient; CAMARA error mapping, timeouts, breaker.

from camara_client import CarrierConfig, LineRef, make_client
client = make_client(CarrierConfig.from_env())
result = await client.sim_swap_check(LineRef(line_id, e164), max_age_h=72)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from camara_client.breaker import DEFAULT_BREAKERS, BreakerRegistry, CircuitBreaker
from camara_client.config import CarrierConfig, OAuthConfig, resolve_secret_ref
from camara_client.direct import DirectClient
from camara_client.errors import ERROR_MAP, CarrierError, map_error
from camara_client.gateway import GatewayClient, load_tool_names, resolve_tool_names, tools_manifest
from camara_client.protocol import (
    CarrierClient,
    CFResult,
    LineRef,
    NumberVerifyResult,
    ReachResult,
    SimSwapResult,
    SubscriptionKind,
)
from camara_client.timeouts import PROFILES, Profile, profile_for


def make_client(
    config: CarrierConfig,
    *,
    secret: str | None = None,
    env: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    gateway_factory: Callable[[], Any] | None = None,
    gateway_auth: Any = None,
    breakers: BreakerRegistry | None = None,
) -> DirectClient | GatewayClient:
    """Build the client `config.client` names. `backend` is never looked at: mock and sandbox differ
    only in base URL and credentials. `secret` is the resolved client secret (Identity / Secrets
    Manager on AWS); when omitted, a local `env:NAME` `secret_ref` is read from the environment."""
    if config.client == "gateway":
        return GatewayClient(
            config, client_factory=gateway_factory, gateway_auth=gateway_auth, breakers=breakers
        )
    if secret is None:
        secret = resolve_secret_ref(config.oauth.secret_ref, env)
    return DirectClient(config, secret=secret, transport=transport, breakers=breakers)


__all__ = [
    "DEFAULT_BREAKERS",
    "ERROR_MAP",
    "PROFILES",
    "BreakerRegistry",
    "CFResult",
    "CarrierClient",
    "CarrierConfig",
    "CarrierError",
    "CircuitBreaker",
    "DirectClient",
    "GatewayClient",
    "LineRef",
    "NumberVerifyResult",
    "OAuthConfig",
    "Profile",
    "ReachResult",
    "SimSwapResult",
    "SubscriptionKind",
    "load_tool_names",
    "make_client",
    "map_error",
    "profile_for",
    "resolve_secret_ref",
    "resolve_tool_names",
    "tools_manifest",
]
