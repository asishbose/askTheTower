"""The carrier side of the bind flow: the authorize URL, and building the `CarrierClient` from the environment.

`DirectClient` locally (and wherever Gateway is not used); `GatewayClient` on AWS, where AgentCore Identity holds
the `binding-page` client secret and exchanges the code. Either way the page calls `number_verify(code, ...)`
without a number, i.e. `phoneNumberShare`: the carrier asserts the number, the user never types it.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlencode

import httpx
from camara_client import CarrierConfig, DirectClient, GatewayClient, make_client
from camara_client import specs as camara_specs

from binding_page.config import carrier_env


def authorization_url(config: CarrierConfig, client: object, redirect_uri: str, state: str) -> str:
    """The carrier's Number Verification authorize URL for this flow."""
    if isinstance(client, DirectClient):
        return client.authorization_url(redirect_uri, state)
    authorize = config.oauth.authorize_url or f"{config.base_url.rstrip('/')}/oauth2/authorize"
    scope = " ".join(
        dict.fromkeys(
            s
            for op_id in ("phoneNumberVerify", "phoneNumberShare")
            for s in camara_specs.operation("number-verification", op_id).scope_for().split()
        )
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": config.oauth.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": scope,
        }
    )
    return f"{authorize}?{query}"


def client_from_env(
    env: Mapping[str, str] | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[CarrierConfig, DirectClient | GatewayClient]:
    e = carrier_env(env)
    config = CarrierConfig.from_env(e)
    return config, make_client(config, env=e, transport=transport)
