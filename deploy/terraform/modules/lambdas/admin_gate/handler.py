"""`admin_gate`: the demo admin path's Lambda REQUEST authorizer (10 §2.1; U2, decision 1 of 2026-10-11).

HTTP API, payload format 2.0, simple responses: `{"isAuthorized": bool}`. Allows a request iff both hold:
- `requestContext.http.sourceIp` lies in one of the networks in `ALLOWED_CIDRS` (a JSON list, `demo_admin_cidrs`).
  `X-Forwarded-For` and every other address header are ignored: through CloudFront the source is CloudFront, so it
  is denied;
- the `Authorization` header is exactly `Bearer <DEMO_ADMIN_BEARER>` (constant-time compare). `DEMO_ADMIN_BEARER` is
  Terraform's `random_password.demo_admin_bearer`, rotated whenever `demo_admin_cidrs` changes; it is never the
  Alerts internal bearer.
Fails closed: a malformed list, a malformed address, an empty list, or an unset bearer denies. Standard library only
(zip Lambda). Behind it each service checks the same bearer again (mock G1, Alerts' admin routes, the binding page's
bearer mode), so a route that lost its authorizer would still refuse.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
from typing import Any

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_cidrs(raw: str) -> tuple[Network, ...]:
    """The allowed networks, or none at all if any entry (or the JSON) is malformed."""
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            return ()
        return tuple(ipaddress.ip_network(str(c), strict=False) for c in items)
    except ValueError:  # json.JSONDecodeError is a ValueError, and so is a bad network
        return ()


def is_allowed(source_ip: object, networks: tuple[Network, ...]) -> bool:
    if not isinstance(source_ip, str) or not networks:
        return False
    try:
        addr = ipaddress.ip_address(source_ip.strip())
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return any(addr.version == n.version and addr in n for n in networks)


def source_ip(event: dict[str, Any]) -> object:
    return ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp")


def authorization(event: dict[str, Any]) -> str | None:
    """The `Authorization` header (payload 2.0 lower-cases header names; matched case-insensitively anyway)."""
    headers = event.get("headers")
    if not isinstance(headers, dict):
        return None
    for name, value in headers.items():
        if isinstance(name, str) and name.lower() == "authorization" and isinstance(value, str):
            return value
    return None


def has_bearer(header: str | None, expected: str) -> bool:
    if not expected or header is None:
        return False
    return hmac.compare_digest(header.encode(), f"Bearer {expected}".encode())


def handler(event: dict[str, Any], context: Any = None) -> dict[str, bool]:
    networks = parse_cidrs(os.environ.get("ALLOWED_CIDRS", "[]"))
    expected = os.environ.get("DEMO_ADMIN_BEARER", "")
    # Both checks always run: the answer takes no shortcut that would tell an address from a bearer failure.
    address_ok = is_allowed(source_ip(event), networks)
    bearer_ok = has_bearer(authorization(event), expected)
    return {"isAuthorized": address_ok and bearer_ok}
