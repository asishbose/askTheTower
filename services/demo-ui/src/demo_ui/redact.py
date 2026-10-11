"""The output filter (doc 11 §8.3): defence in depth on every HTML fragment and SSE frame.

The UI never reads a number (it uses the mock's `ref`s and the services' redacted views), so this should never
fire. It masks with the shared phone-number rule (`ref_client.privacy`, decision 1: `+` and 8+ digits, grouped
or not; a bare run of 10+ digits unless the whole token is an ISO-8601 date-time or epoch-ms), the same rule the
privacy tests use. The rule leaves bare grouped numbers alone, so the North-American shapes (`(613) 555-0101`,
`613.555.0101`, `1 613 555 0101`) are masked here too.
"""

from __future__ import annotations

import re

from ref_client import privacy
from ref_client.privacy import REDACTED

GROUPED = re.compile(r"(?:\+?\d{1,3}[\s.-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")


def mask(text: str) -> str:
    return str(GROUPED.sub(REDACTED, privacy.mask(text)))
