"""Signed values: the OAuth `state`, the bind-flow cookie, the page session cookie and CSRF tokens.

Format: `<base64url(JSON payload)>.<HMAC-SHA256 hex, re-lettered a-p>`. The signature is re-lettered so that
no signed value can ever match the phone-number regex (same trick as `line_id`). Every payload carries
`x` (expiry, epoch seconds) and `p` (purpose), so a value signed for one purpose is useless for another.

**OAuth state design.** `POST /bind/{token}/verify` creates a random nonce `n` and sends the browser off with
`state = sign({p:"state", u:<user_id from the token>, n, x:+10 min})`; the bind token itself never leaves this
site: it rides in an HttpOnly cookie `atb_flow = sign({p:"flow", t:<token>, n, x})` scoped to `/bind`. The
callback requires both, with the same `n` (ties the redirect to the browser that started it — login CSRF),
then consumes the token *for `u`* (`consume_bind_token` refuses a token of another user).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import string
from typing import Any

_HEX_TO_LETTERS = str.maketrans("0123456789abcdef", "abcdefghijklmnop")

SESSION_COOKIE = "atb_session"
FLOW_COOKIE = "atb_flow"


def nonce(n: int = 24) -> str:
    return "".join(secrets.choice(string.ascii_letters) for _ in range(n))


class Signer:
    def __init__(self, secret: str) -> None:
        self._key = hashlib.sha256(b"binding-page/v1|" + secret.encode()).digest()

    def __repr__(self) -> str:
        return "Signer(key=<redacted>)"

    def _mac(self, data: bytes) -> str:
        return hmac.new(self._key, data, hashlib.sha256).hexdigest().translate(_HEX_TO_LETTERS)

    def sign(self, purpose: str, payload: dict[str, Any], *, expires_at: int) -> str:
        body = dict(payload, p=purpose, x=expires_at)
        raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).rstrip(b"=")
        return f"{raw.decode()}.{self._mac(raw)}"

    def unsign(self, purpose: str, value: str | None, *, now: int) -> dict[str, Any] | None:
        """The payload, or None when the value is missing, forged, for another purpose or expired."""
        if not value or value.count(".") != 1:
            return None
        raw, mac = value.split(".")
        if not hmac.compare_digest(self._mac(raw.encode()), mac):
            return None
        try:
            body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        except (binascii.Error, ValueError):
            return None
        if not isinstance(body, dict) or body.get("p") != purpose:
            return None
        x = body.get("x")
        if not isinstance(x, int) or x <= now:
            return None
        return body

    def csrf(self, session_value: str) -> str:
        """Per-session CSRF token for the page's POST forms."""
        return self._mac(b"csrf|" + session_value.encode())

    def check_csrf(self, session_value: str, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(self.csrf(session_value), token or "")
