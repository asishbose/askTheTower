"""Decision 1 (2026-10-11): the shared phone-number rule as the privacy tests see it (`phone_hits`)."""

import random
import re

import pytest
from ref_client import privacy
from ref_client.privacy import mask

from tests.privacy import patterns
from tests.privacy.patterns import (
    DATE_TIME_ALLOWLIST,
    DIGIT_RUN,
    E164_STRICT,
    PLUS_NUMBER,
    TOKEN_CHAR,
    is_date_time,
    phone_hits,
    phone_spans,
)

pytestmark = pytest.mark.unit

ALLOWLISTED = [
    "2026-10-05T14:12:00Z",
    "20261005T141200Z",
    "2026-10-05 14:12",
    "2026-10-05T14:12:00+05:30",
    "20261005141200",
    "20261005141200Z",
    "20261005141200+0530",
    "2026-10-05T14:12:00.123456Z",
    "1791209520000",  # epoch-ms, 2026-10-05T14:12:00Z
]


@pytest.mark.parametrize("token", ALLOWLISTED)
def test_allowlisted_date_time_shapes_are_not_hits(token: str) -> None:
    assert phone_hits(token) == []
    assert phone_hits(f'{{"at": "{token}"}} and at {token}.') == []


@pytest.mark.parametrize(
    ("token", "shape"),
    [
        ("2026-10-05", 0),
        ("2026-10-05T14:12", 0),
        ("2026-10-05T14:12:00Z", 0),
        ("2026-10-05T14:12:00-04:00", 0),
        ("2026-10-05T14:12:00.123+0530", 0),
        ("20261005T1412", 1),
        ("20261005T141200Z", 1),
        ("20261005T141200+05", 1),
        ("20261005141200", 2),
        ("20261005141200.5Z", 2),
        ("20261005141200-0400", 2),
        ("1000000000000", 3),  # epoch-ms 2001-09-09
        ("1791209520000", 3),
        ("1999999999999", 3),  # epoch-ms 2033-05-18
    ],
)
def test_allowlist_accepts_each_iso_and_epoch_ms_shape(token: str, shape: int) -> None:
    assert DATE_TIME_ALLOWLIST[shape].fullmatch(token), token
    assert is_date_time(token)


@pytest.mark.parametrize(
    "token",
    [
        "6135550101",  # 10 digits, a North-American number
        "1791209520",  # epoch seconds: not allowed
        "202610051412",  # YYYYMMDDhhmm without T: seconds required
        "20261305141200",  # month 13
        "20261032141200",  # day 32
        "20261005241200",  # hour 24
        "2026-10-05T24:00:00Z",
        "18261005141200",  # year 1826
        "2791209520000",  # 13 digits not starting with 1
        "17912095200001",  # 14 digits starting with 1
        "2026-10-05T14:12:00Z6135550101",  # a date with a number glued on
        "20261005141200x",
        "line:20261005141200",
    ],
)
def test_allowlist_refuses_near_misses(token: str) -> None:
    assert not is_date_time(token), token
    assert len(DATE_TIME_ALLOWLIST) == 4


def test_plus_number_is_eight_to_fifteen_digits_and_ignores_the_allowlist() -> None:
    assert PLUS_NUMBER.fullmatch("+12345678") and PLUS_NUMBER.fullmatch("+123456789012345")
    assert not PLUS_NUMBER.fullmatch("+1234567") and not PLUS_NUMBER.fullmatch("+1234567890123456")
    assert phone_hits("+20261005141200") == ["+20261005141200"]
    assert phone_hits("+1791209520000") == ["+1791209520000"]


def test_runs_past_fifteen_digits_are_covered_to_their_end() -> None:
    """`E164_STRICT` matches inside a longer run, so the rule masks the whole of it: no tail is left behind."""
    assert phone_hits("x +1234567890123456789 y") == ["+1234567890123456789"]
    assert phone_hits("x 1234567890123456789 y") == ["1234567890123456789"]
    assert DIGIT_RUN.fullmatch("1234567890") and not DIGIT_RUN.fullmatch("123456789")


def test_the_runtime_copy_is_this_rule() -> None:
    """The demo UI masks with `ref_client.privacy` (images carry no `tests/`): same patterns, same spans."""
    assert privacy.PLUS_NUMBER.pattern == PLUS_NUMBER.pattern
    assert privacy.DIGIT_RUN.pattern == DIGIT_RUN.pattern
    assert privacy.TOKEN_CHAR.pattern == TOKEN_CHAR.pattern
    assert [p.pattern for p in privacy.DATE_TIME_ALLOWLIST] == [p.pattern for p in DATE_TIME_ALLOWLIST]
    rnd = random.Random(1011)  # noqa: S311 - seeded test data, not a secret
    alphabet = "0123456789" * 4 + "+-.:() TZab"
    for _ in range(2000):
        text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(8, 40)))
        assert privacy.phone_spans(text) == phone_spans(text), text


def test_patterns_defines_the_rule_without_importing_the_runtime_copy() -> None:
    source = open(patterns.__file__, encoding="utf-8").read()
    assert "DATE_TIME_ALLOWLIST: tuple" in source and "ref_client" not in source.split('"""', 2)[2]


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-05 6135550101",
        "2026-10-056135550101",
        "20261005141200 6135550101",
        "202610051412006135550101",
        "20261005141200x",
        "line:20261005141200",
        "2026-10-05T14:12:00Z6135550101",
    ],
)
def test_a_number_next_to_or_glued_to_a_date_is_still_a_hit(text: str) -> None:
    assert phone_hits(text) != []


@pytest.mark.parametrize(
    "text", ["+12345678", "+44 20 7946 0958", "+61 491 570 156", "+33 1 99 00 12 34", "+49.30.1234.5678"]
)
def test_plus_and_eight_or_more_digits_is_a_hit(text: str) -> None:
    assert phone_hits(f"call {text} now") == [text]


@pytest.mark.parametrize("text", ["123456789", "+1234567", "+05:30", "1234 ms", "14:12:00"])
def test_short_runs_are_not_hits(text: str) -> None:
    assert phone_hits(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "6135550101",  # 10 digits; also epoch seconds, which the rule does not allow
        "16135550101",
        "202610051412",  # YYYYMMDDhhmm without seconds: a 12-digit E.164 shape
        "2026100514120",  # 13 digits, not starting with 1
        "20261305141200",  # month 13
        "442071838750",
        "123456789012345",
        "1234567890123456789",  # longer than 15: E164_STRICT matched inside it, so the rule still does
    ],
)
def test_bare_ten_or_more_digits_off_the_allowlist_is_a_hit(text: str) -> None:
    assert phone_hits(f"id {text} here") == [text]


def _token(text: str, start: int, end: int) -> str:
    while start > 0 and TOKEN_CHAR.match(text[start - 1]):
        start -= 1
    while end < len(text) and TOKEN_CHAR.match(text[end]):
        end += 1
    return text[start:end].rstrip(".:")


def test_nothing_the_strict_regex_caught_escapes_except_allowlisted_tokens() -> None:
    """Seeded sweep: after masking with the shared rule, a 10+ digit run survives only as a whole allowlisted
    token, and `phone_hits` finds nothing."""
    rnd = random.Random(20261011)  # noqa: S311 - seeded test data, not a secret
    alphabet = "0123456789" * 4 + "+-.:() TZab"
    for _ in range(3000):
        text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(8, 40)))
        out = mask(text)
        assert phone_hits(out) == [], (text, out)
        for m in E164_STRICT.finditer(out):
            run = re.match(r"\d+", out[m.start() :])
            assert run and is_date_time(_token(out, m.start(), m.start() + run.end())), (text, out)
    for t in ["x6135550101y", "abc+16135550101", "a1b16135550101", "9" * 40]:
        assert phone_hits(t), t
