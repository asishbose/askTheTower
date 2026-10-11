"""The one place privacy regexes live for tests. Every privacy test imports from here (prompt 18 asserts it).

The phone-number rule (human decision 1, 2026-10-11) is defined here: `PLUS_NUMBER`, `DIGIT_RUN`,
`DATE_TIME_ALLOWLIST`, `phone_spans`, `phone_hits`. The demo UI masks with the same rule, but product code can't
import `tests/`, so `ref_client/privacy.py` carries a runtime copy that `tests/privacy/test_phone_rule.py` holds
identical to this one. `E164_STRICT` stays the conventions' regex, verbatim."""

import re

# E.164-ish: optional +, 10+ digits with common separators between groups (asserted absent after masking).
E164 = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{8,}\d(?![\w.])")
# The 00-conventions regex (no separators). `phone_hits` catches everything it does, minus allowlisted dates.
E164_STRICT = re.compile(r"\+?\d{10,15}")
HEALTH_WORDS = re.compile(r"\b(fall|fallen|emergency|unwell|911|stroke|collapsed|injur\w*)\b", re.I)
AWS_KEY_ID = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")
BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._-]{20,}")

ALLOWED_PHONE_FORMS = re.compile(r"(?:•{4}|\*{4}|x{4})\s?\d{4}")  # masked last-four is allowed

# Decision 1, rule (a): `+` then 8–15 digits, grouped or not. Up to two separators between digits covers `) `.
# Always a hit: the allowlist never applies to it.
PLUS_NUMBER = re.compile(r"\+\(?\d(?:[\s.()\-]{0,2}\d){7,14}")
# Decision 1, rule (b): a bare digit run holding a 10–15 digit match. A longer run holds one too (`E164_STRICT`
# matches inside it), so the span is the whole maximal run and the rule never catches less than the conventions.
DIGIT_RUN = re.compile(r"(?<!\d)\d{10,}(?!\d)")
# The token an allowlist shape has to cover entirely: a date can't hide a number beside or glued to it.
TOKEN_CHAR = re.compile(r"[0-9A-Za-z:.+\-]")

_DATE = r"(?:19|20)\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
_BASIC_DATE = r"(?:19|20)\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
_TIME = r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,9})?)?"
_BASIC_TIME = r"(?:[01]\d|2[0-3])[0-5]\d(?:[0-5]\d(?:\.\d{1,9})?)?"
_BASIC_TIME_S = r"(?:[01]\d|2[0-3])[0-5]\d[0-5]\d(?:\.\d{1,9})?"
_ZONE = r"(?:Z|[+-](?:[01]\d|2[0-3])(?::?[0-5]\d)?)?"

# Decision 1: the only shapes a bare 10–15 digit run may sit in. Matched against the whole token (fullmatch).
DATE_TIME_ALLOWLIST: tuple[re.Pattern[str], ...] = (
    # ISO-8601 extended: 2026-10-05, 2026-10-05T14:12:00Z, 2026-10-05T14:12:00.123+05:30
    re.compile(rf"{_DATE}(?:T{_TIME}{_ZONE})?"),
    # ISO-8601 basic with T: 20261005T141200Z, 20261005T1412+0530
    re.compile(rf"{_BASIC_DATE}T{_BASIC_TIME}{_ZONE}"),
    # ISO-8601 basic without T: seconds required. `YYYYMMDDhhmm` is also a 12-digit E.164 shape (country code 20).
    re.compile(rf"{_BASIC_DATE}{_BASIC_TIME_S}{_ZONE}"),
    # Epoch-ms, 13 digits from 2001-09-09 to 2033-05-18: no E.164 number is 13 digits starting with 1 (country
    # code 1 numbers have 11). Epoch seconds are not allowed: 10 digits is exactly a North-American number.
    re.compile(r"1\d{12}"),
)


def _token(text: str, start: int, end: int) -> str:
    """The token around `text[start:end]`: neighbouring letters, digits and `:.+-`, minus a trailing `.`/`:`."""
    while start > 0 and TOKEN_CHAR.match(text[start - 1]):
        start -= 1
    while end < len(text) and TOKEN_CHAR.match(text[end]):
        end += 1
    return text[start:end].rstrip(".:")


def is_date_time(token: str) -> bool:
    """True if the whole `token` is one of the `DATE_TIME_ALLOWLIST` shapes."""
    return any(p.fullmatch(token) for p in DATE_TIME_ALLOWLIST)


def phone_spans(text: str) -> list[tuple[int, int]]:
    """Spans of phone-number hits in `text` by decision 1, in order, not overlapping.

    A `+` number longer than 15 digits is covered to the end of its digit run, so masking leaves no tail."""
    plus = [[*m.span()] for m in PLUS_NUMBER.finditer(text)]
    spans: list[tuple[int, int]] = []
    for m in DIGIT_RUN.finditer(text):
        owner = next((p for p in plus if p[0] <= m.start() < p[1]), None)
        if owner is not None:
            owner[1] = max(owner[1], m.end())
        elif not is_date_time(_token(text, m.start(), m.end())):
            spans.append(m.span())
    return sorted(spans + [(s, e) for s, e in plus])


def phone_hits(text: str) -> list[str]:
    """Phone-number hits by decision 1 (2026-10-11).

    `+` then 8–15 digits, grouped or not, is always a hit; a bare 10–15 digit run is a hit unless its whole
    token matches `DATE_TIME_ALLOWLIST` (ISO-8601 with or without separators, `T`/`Z`/offset; epoch-ms). A
    masked last-four (`ALLOWED_PHONE_FORMS`) has four digits, so it is never a hit."""
    return [text[s:e] for s, e in phone_spans(text)]
