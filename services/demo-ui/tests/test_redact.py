"""Doc 11 §8.3: the output filter masks number shapes and leaves times, ids and refs alone."""

from __future__ import annotations

import logging
import random
import string

import pytest
from demo_ui.__main__ import JsonFormatter
from demo_ui.feed import short, short_ref
from demo_ui.redact import mask

from tests.privacy.patterns import E164, E164_STRICT, phone_hits

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text",
    ["+16135550101", "16135550101", "+1 613 555 0101", "(613) 555-0101", "613.555.0101", "613-555-0101"],
)
def test_number_shapes_are_masked(text: str) -> None:
    out = mask(f"call {text} now")
    assert "[redacted]" in out and phone_hits(out) == []
    assert "555" not in out


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-05T14:12:00Z",
        "2026-10-05 14:12",
        "14:12:00",
        "line:3f2a9c0d1e4b5a6c",
        "SIM_SWAPPED_RECENT.sms",
        "moment-1#3",
        "1234 ms",
    ],
)
def test_ordinary_text_is_kept(text: str) -> None:
    assert mask(text) == text


# --- step 3 (doc 11 §8.3, §11): nothing E.164 survives; refs and line ids are never touched ----------------------

ALNUM = string.ascii_letters + " ,;:!?()[]/_'\"<>="


def test_no_e164_survives_a_seeded_sweep() -> None:
    """500 numbers, 10–15 digits, with and without `+`, in random text: none comes out (seeded, deterministic)."""
    rnd = random.Random(20261009)  # noqa: S311 - seeded test data, not a secret
    for _ in range(500):
        number = ("+" if rnd.random() < 0.5 else "") + "".join(
            rnd.choice(string.digits) for _ in range(rnd.randint(10, 15))
        )
        pre = "".join(rnd.choice(ALNUM) for _ in range(rnd.randint(0, 12)))
        post = "".join(rnd.choice(ALNUM) for _ in range(rnd.randint(0, 12)))
        out = mask(f"{pre}{number}{post}")
        assert E164_STRICT.search(out) is None, (number, out)
        assert phone_hits(out) == []


@pytest.mark.parametrize(
    "text",
    [
        "+1-613-555-0101",
        "1 613 555 0101",
        "+1 (613) 555-0101",
        # doc 11 §8.3: "any run of 10 or more digits split by spaces, dots or dashes", not only the 3-3-4 shape
        "+44 20 7946 0958",
        "+61 491 570 156",
        "+33 1 99 00 12 34",
        "+49.30.1234.5678",
    ],
    # Ids without digits: the test name lands in junit and artifacts/test-report.md, which the privacy grep reads.
    ids=["na-dashed", "na-spaced", "na-parens", "uk-grouped", "au-grouped", "fr-grouped", "de-grouped"],
)
def test_grouped_numbers_are_masked(text: str) -> None:
    out = mask(f"call {text} now")
    assert out == "call [redacted] now", out
    assert E164.search(out) is None


def test_shortened_refs_and_line_ids_are_never_masked() -> None:
    """The UI shows `line:` + 8 hex and `line_id[:8]`; even an all-digit ref, shortened, is left alone (a full
    16-digit one would not be: that is why the panes shorten it)."""
    rnd = random.Random(7)  # noqa: S311 - seeded test data, not a secret
    refs = ["line:" + "".join(rnd.choice("0123456789abcdef") for _ in range(16)) for _ in range(300)]
    refs += ["line:1234567890123456", "line:0000000000000000"]
    for ref in refs:
        shown = short_ref(ref)
        assert len(shown) == len("line:") + 8 and mask(shown) == shown
        assert mask(f'<td class="ref">{shown}</td>') == f'<td class="ref">{shown}</td>'
    assert mask("line:1234567890123456") != "line:1234567890123456"
    line_id = "ln_" + "a" * 64
    assert mask(short(line_id)) == short(line_id) == "ln_aaaaa"


def test_log_lines_are_masked() -> None:
    record = logging.LogRecord("demo_ui", logging.INFO, __file__, 1, "fired on %s", ("+16135550101",), None)
    line = JsonFormatter().format(record)
    assert phone_hits(line) == [] and "[redacted]" in line
