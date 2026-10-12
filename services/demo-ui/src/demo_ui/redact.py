"""The output filter (doc 11 §8.3): defence in depth on every HTML fragment and SSE frame.

The UI never reads a number (it uses the mock's `ref`s and the services' redacted views), so this should never
fire. It masks with the shared phone-number rule (`ref_client.privacy`, decision 1: `+` and 8+ digits, grouped
or not; a bare run of 10+ digits unless the whole token is an ISO-8601 date-time or epoch-ms), the same rule the
privacy tests use. The rule leaves bare grouped numbers alone, so the North-American shapes (`(613) 555-0101`,
`613.555.0101`, `1 613 555 0101`) are masked here too.

`mask_secrets` is for the Ops log and the aws transcripts (doc 11 §2 "The Ops tab"): a child `make` reads the
repo's `.env` files itself, so its output can carry a bearer, a `*_TOKEN=` line, an access-key id, a secret key or a
session token the UI never handed it. The root `.env` is never read for this (T3): these patterns are what mask its
values.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ref_client import privacy
from ref_client.privacy import REDACTED

GROUPED = re.compile(r"(?:\+?\d{1,3}[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")

HIDDEN = "••••"
MIN_SECRET_LEN = 8  # shorter values ("1", "true", "local") would mask ordinary words
BEARER = re.compile(r"(?i)\b(Bearer)\s+[^\s\"',;]+")
# The deny patterns of `ops.allowlist.DENY`, as a name in `NAME=value`, `NAME: value` or `"NAME": "value"`.
SECRET_NAME = (
    r"(?:AWS_\w*|TF_VAR_\w*|COGNITO_\w*|\w*_TOKEN|\w*_BEARER|\w*_SECRET|\w*_PASSWORD|\w*_KEY|VIRTUAL_ENV|ENV)"
)
ASSIGNMENT = re.compile(rf"(?i)\b({SECRET_NAME})(\"?\s*[=:]\s*[\"']?)([^\s\"',;]+)")
ACCESS_KEY_ID = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
# T7: the shared-credentials names in any case, `=` or `:`, quoted or not → `<name>=••••`.
AWS_CREDENTIAL = re.compile(
    r"(?i)\b(aws_secret_access_key|aws_session_token|aws_access_key_id)[\"']?\s*[=:]\s*[\"']?[^\s\"',;]+[\"']?"
)
_B64 = r"A-Za-z0-9/+="
# An AWS secret key: exactly 40 of [A-Za-z0-9/+=], bounded, with upper, lower and a digit, `/` or `+` (a 40-hex git
# sha has no upper case and survives).
SECRET_SHAPED = re.compile(
    rf"(?<![{_B64}])(?=[{_B64}]{{40}}(?![{_B64}]))(?=[^A-Z]{{0,39}}[A-Z])(?=[^a-z]{{0,39}}[a-z])"
    rf"(?=[^0-9/+]{{0,39}}[0-9/+])[{_B64}]{{40}}(?![{_B64}])"
)
LONG_RUN = re.compile(rf"[{_B64}]{{100,}}")  # session tokens


def mask(text: str) -> str:
    return str(GROUPED.sub(REDACTED, privacy.mask(text)))


def mask_secrets(text: str, secrets: Iterable[str] = ()) -> str:
    """`Bearer x` → `Bearer ••••`; `aws_secret_access_key = x` → `aws_secret_access_key=••••`; `FOO_TOKEN=x` →
    `FOO_TOKEN=••••`; `AKIA…`/`ASIA…` ids; secret-shaped 40-character runs; runs of 100+ base64 characters; and
    every known secret value of at least `MIN_SECRET_LEN` characters, longest first (one may contain another)."""
    for value in sorted({s for s in secrets if len(s) >= MIN_SECRET_LEN}, key=len, reverse=True):
        text = text.replace(value, HIDDEN)
    text = BEARER.sub(rf"\1 {HIDDEN}", text)
    text = AWS_CREDENTIAL.sub(rf"\1={HIDDEN}", text)
    text = ASSIGNMENT.sub(rf"\1\2{HIDDEN}", text)
    text = ACCESS_KEY_ID.sub(HIDDEN, text)
    text = LONG_RUN.sub(HIDDEN, text)
    return SECRET_SHAPED.sub(HIDDEN, text)
