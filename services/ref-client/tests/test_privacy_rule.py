"""Decision 1 (2026-10-11): `ref_client.privacy`, the phone-number rule shared by the privacy tests and the
demo UI. The cases as the privacy tests see them are in `tests/privacy/test_phone_rule.py`."""

from __future__ import annotations

import pytest
from ref_client.privacy import DATE_TIME_ALLOWLIST, REDACTED, is_date_time, mask, phone_numbers, phone_spans

pytestmark = pytest.mark.unit


def test_mask_replaces_every_hit_and_keeps_the_rest() -> None:
    text = "at 2026-10-05T14:12:00Z call +1 (613) 555-0101, or 6135550102; ts 1791209520000."
    assert mask(text) == f"at 2026-10-05T14:12:00Z call {REDACTED}, or {REDACTED}; ts 1791209520000."
    assert mask("") == "" and mask("nothing here") == "nothing here"


def test_spans_are_ordered_and_do_not_overlap() -> None:
    text = "6135550101 then +16135550102 then 6135550103"
    spans = phone_spans(text)
    assert spans == sorted(spans)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
    assert phone_numbers(text) == ["6135550101", "+16135550102", "6135550103"]


def test_plus_rule_ignores_the_allowlist() -> None:
    assert phone_numbers("+20261005141200") == ["+20261005141200"]
    assert phone_numbers("+1791209520000") == ["+1791209520000"]


@pytest.mark.parametrize(
    ("token", "ok"),
    [
        ("2026-10-05", True),
        ("2026-10-05T14:12", True),
        ("2026-10-05T14:12:00-04:00", True),
        ("20261005T1412", True),
        ("20261005141200.5Z", True),
        ("1000000000000", True),
        ("1999999999999", True),
        ("2026-10-05T24:00:00Z", False),
        ("20261005241200", False),
        ("1791209520", False),  # epoch seconds: not allowed
        ("2791209520000", False),
        ("17912095200001", False),
    ],
)
def test_allowlist_shapes(token: str, ok: bool) -> None:
    assert is_date_time(token) is ok
    assert len(DATE_TIME_ALLOWLIST) == 4


def test_a_trailing_full_stop_or_colon_is_not_part_of_the_token() -> None:
    assert phone_numbers("at 20261005141200.") == [] and phone_numbers("at 20261005141200:") == []
    # The token is the whole `20261005141200.6135550101`, which is no allowlist shape: both runs are hits.
    assert phone_numbers("at 20261005141200.6135550101") == ["20261005141200", "6135550101"]


def test_transcript_gates_use_both_rules() -> None:
    """Transcripts, chat input and `next_step` refuse the shared rule's hits *and* every strict-regex run."""
    from ref_client.transcript import has_number, redact

    assert redact("call +44 20 7946 0958 now") == f"call {REDACTED} now"
    assert redact("at 20261005141200") == f"at {REDACTED}"  # strict: the runtime gate keeps refusing dates
    assert redact("at 2026-10-05T14:12:00Z") == "at 2026-10-05T14:12:00Z"
    assert has_number("+61 491 570 156") and has_number("1791209520000") and has_number("6135550101")
    assert not has_number("at 2026-10-05T14:12:00Z, 1234 ms")
