"""AWS wiring for `GatewayClient` (prompt 13): SigV4 inbound auth to AgentCore Gateway.

The Gateway is deployed with `authorizer_type = AWS_IAM` (deploy/terraform/modules/agentcore_gateway), so every MCP
request from Tower (AgentCore Runtime role) or a Lambda is signed with SigV4 for the `bedrock-agentcore` service.
No carrier credential is involved: Gateway gets those from AgentCore Identity.

`SigV4Auth` is an `httpx2.Auth` (the HTTP client fastmcp 4 uses). It signs `host`, `content-type` and the body
hash with the process's default AWS credentials, re-read per request so rotated role credentials are picked up.
botocore is imported lazily: nothing here is needed locally.

    CARRIER_GATEWAY_AUTH=sigv4|none   (default none)
    CARRIER_GATEWAY_REGION            (default AWS_REGION / AWS_DEFAULT_REGION)
"""

from __future__ import annotations

import os
from collections.abc import Generator, Mapping
from typing import Any

import httpx2

SERVICE = "bedrock-agentcore"
_COPY = ("Authorization", "X-Amz-Date", "X-Amz-Security-Token")


class SigV4Auth(httpx2.Auth):
    """Sign each request with SigV4. `credentials` is anything with `get_frozen_credentials()` (a botocore
    `Credentials`), or None for the default chain."""

    requires_request_body = True

    def __init__(self, region: str, *, service: str = SERVICE, credentials: Any = None) -> None:
        if not region:
            raise ValueError("SigV4 needs a region (CARRIER_GATEWAY_REGION or AWS_REGION)")
        self.region = region
        self.service = service
        self._credentials = credentials

    def __repr__(self) -> str:
        return f"SigV4Auth(region={self.region!r}, service={self.service!r})"

    def _frozen(self) -> Any:
        creds = self._credentials
        if creds is None:
            import botocore.session

            creds = botocore.session.get_session().get_credentials()
            if creds is None:
                raise RuntimeError("no AWS credentials for SigV4 (Runtime/Lambda role expected)")
            self._credentials = creds
        return creds.get_frozen_credentials()

    def sign(self, method: str, url: str, headers: Mapping[str, str], body: bytes) -> dict[str, str]:
        """The headers to add: Authorization, X-Amz-Date and (temporary credentials) X-Amz-Security-Token."""
        from botocore.auth import SigV4Auth as BotoSigV4
        from botocore.awsrequest import AWSRequest

        signed = {k: v for k, v in headers.items() if k.lower() in ("host", "content-type")}
        req = AWSRequest(method=method, url=url, data=body, headers=signed)
        BotoSigV4(self._frozen(), self.service, self.region).add_auth(req)
        return {k: req.headers[k] for k in _COPY if k in req.headers}

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        headers = {"host": request.headers.get("host") or request.url.netloc.decode("ascii")}
        if "content-type" in request.headers:
            headers["content-type"] = request.headers["content-type"]
        for k, v in self.sign(request.method, str(request.url), headers, request.content).items():
            request.headers[k] = v
        yield request


def gateway_auth_from_env(env: Mapping[str, str] | None = None) -> httpx2.Auth | None:
    """`CARRIER_GATEWAY_AUTH=sigv4` → `SigV4Auth`; `none` (default) → None (in-process / local Gateway)."""
    e = os.environ if env is None else env
    mode = (e.get("CARRIER_GATEWAY_AUTH") or "none").strip().lower()
    if mode == "none":
        return None
    if mode == "sigv4":
        region = e.get("CARRIER_GATEWAY_REGION") or e.get("AWS_REGION") or e.get("AWS_DEFAULT_REGION") or ""
        return SigV4Auth(region)
    raise ValueError(f"CARRIER_GATEWAY_AUTH must be sigv4 or none, got {mode!r}")
