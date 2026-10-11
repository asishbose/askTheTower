"""`policy_version()` — a hash of the thresholds file and the phrasing templates.

Written into every audit row (07) so a row can be read against the exact rules and words that
produced it. Changes to either file change the version.
"""

import hashlib
import json
from functools import cache

from tower_policy.phrasing import TEMPLATES
from tower_policy.thresholds import DEFAULT_THRESHOLDS_PATH


@cache
def policy_version() -> str:
    """sha256 over the packaged `thresholds.yaml` bytes and the canonical JSON of `TEMPLATES`."""
    h = hashlib.sha256()
    h.update(DEFAULT_THRESHOLDS_PATH.read_bytes())
    h.update(b"\n--templates--\n")
    canonical = json.dumps(
        {code.value: dict(forms) for code, forms in TEMPLATES.items()}, sort_keys=True, ensure_ascii=True
    )
    h.update(canonical.encode("utf-8"))
    return h.hexdigest()
