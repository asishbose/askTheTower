"""Bearer extraction and pass-through for the HTTP app (09 §6.2 rules 1 and 3).

The agent does not verify the token: the Runtime's JWT authorizer and then Tower do (01 §4). It only refuses a
request with no `Authorization: Bearer <token>`, or a malformed one, so an anonymous request costs no model call.
The header that passes is forwarded to Tower byte for byte.
"""

from __future__ import annotations

import re

# RFC 6750 §2.1: the scheme is case-insensitive; the credential is a b64token, nothing else on the line.
_BEARER = re.compile(r"(?i:bearer) [A-Za-z0-9\-._~+/]+=*")
MAX_HEADER_LEN = 8192  # far above a Cognito access token; refuses a pathological header before any work


def bearer_header(value: str | None) -> str | None:
    """The `Authorization` header to forward to Tower, unchanged, or None when it is missing or malformed."""
    if not value or len(value) > MAX_HEADER_LEN:
        return None
    return value if _BEARER.fullmatch(value) else None
