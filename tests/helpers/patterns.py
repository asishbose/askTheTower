"""Re-export of the privacy regexes. The definitions live in `tests/privacy/patterns.py` and only there."""

from tests.privacy.patterns import (
    ALLOWED_PHONE_FORMS,
    AWS_KEY_ID,
    BEARER,
    E164,
    E164_STRICT,
    HEALTH_WORDS,
    phone_hits,
)

__all__ = ["ALLOWED_PHONE_FORMS", "AWS_KEY_ID", "BEARER", "E164", "E164_STRICT", "HEALTH_WORDS", "phone_hits"]
