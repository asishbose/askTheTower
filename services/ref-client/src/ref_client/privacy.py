"""The phone-number rule (human decision 1, 2026-10-11), runtime copy for the demo UI and the ref-client.

The rule is defined in `tests/privacy/patterns.py`; the runtime images carry no `tests/`, so this module repeats
it and `tests/privacy/test_phone_rule.py` asserts the two are identical (same patterns, same spans). Design rule 1
still holds: no product component imports `ref_client`; test code and the demo UI may.

A hit is either of:

1. `+` followed by 8–15 digits, with spaces, dots, dashes or parentheses between digit groups
   (`+44 20 7946 0958`, `+1 (613) 555-0101`). The allowlist never applies to this shape. A longer `+` number is
   covered to the end of its digit run.
2. A bare run holding a 10–15 digit match, unless the whole token around it (letters, digits and `:.+-`) is one
   of the `DATE_TIME_ALLOWLIST` shapes. A run longer than 15 digits is a hit as a whole: the strict regex of the
   conventions (`\\+?\\d{10,15}`) matches inside it, and this rule may not catch less than it does.

Grouped numbers without `+` (`(613) 555-0101`) are not in the rule; the demo UI masks those too (`GROUPED`).
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"

__all__ = [
    "DATE_TIME_ALLOWLIST",
    "DIGIT_RUN",
    "PLUS_NUMBER",
    "REDACTED",
    "TOKEN_CHAR",
    "is_date_time",
    "mask",
    "phone_numbers",
    "phone_spans",
]

# Rule 1: `+`, then 8–15 digits; up to two separator characters between digits covers `) ` and ` (`.
PLUS_NUMBER = re.compile(r"\+\(?\d(?:[\s.()\-]{0,2}\d){7,14}")
# Rule 2: a maximal digit run holding a 10–15 digit match.
DIGIT_RUN = re.compile(r"(?<!\d)\d{10,}(?!\d)")
# The token an allowlist shape has to cover entirely: a date can't hide a number glued to it.
TOKEN_CHAR = re.compile(r"[0-9A-Za-z:.+\-]")

_DATE = r"(?:19|20)\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
_BASIC_DATE = r"(?:19|20)\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
_TIME = r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,9})?)?"
_BASIC_TIME = r"(?:[01]\d|2[0-3])[0-5]\d(?:[0-5]\d(?:\.\d{1,9})?)?"
_BASIC_TIME_S = r"(?:[01]\d|2[0-3])[0-5]\d[0-5]\d(?:\.\d{1,9})?"
_ZONE = r"(?:Z|[+-](?:[01]\d|2[0-3])(?::?[0-5]\d)?)?"

# ISO-8601 and epoch-ms shapes a bare digit run may sit in. Matched against the whole token (fullmatch).
DATE_TIME_ALLOWLIST: tuple[re.Pattern[str], ...] = (
    # 2026-10-05, 2026-10-05T14:12:00Z, 2026-10-05T14:12:00.123+05:30
    re.compile(rf"{_DATE}(?:T{_TIME}{_ZONE})?"),
    # 20261005T141200Z, 20261005T1412+0530
    re.compile(rf"{_BASIC_DATE}T{_BASIC_TIME}{_ZONE}"),
    # 20261005141200, 20261005141200Z: without the `T`, seconds are required. A 12-digit `YYYYMMDDhhmm` is
    # also a valid 12-digit E.164 shape (country code 20), so it stays a hit.
    re.compile(rf"{_BASIC_DATE}{_BASIC_TIME_S}{_ZONE}"),
    # Epoch milliseconds, 13 digits from 2001-09-09 to 2033-05-18. A 13-digit run starting with 1 is not a
    # valid E.164 number (country code 1 numbers have 11 digits). Epoch *seconds* (10 digits) are not allowed:
    # that is exactly a North-American national number, and the human named epoch-ms only.
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
    """Spans of phone-number hits in `text` (rules 1 and 2 above), in order, not overlapping."""
    plus = [[*m.span()] for m in PLUS_NUMBER.finditer(text)]
    spans: list[tuple[int, int]] = []
    for m in DIGIT_RUN.finditer(text):
        owner = next((p for p in plus if p[0] <= m.start() < p[1]), None)
        if owner is not None:
            owner[1] = max(owner[1], m.end())
        elif not is_date_time(_token(text, m.start(), m.end())):
            spans.append(m.span())
    return sorted(spans + [(s, e) for s, e in plus])


def phone_numbers(text: str) -> list[str]:
    """The phone-number hits in `text`."""
    return [text[s:e] for s, e in phone_spans(text)]


def mask(text: str) -> str:
    """`text` with every phone-number hit replaced by `REDACTED`."""
    out: list[str] = []
    last = 0
    for s, e in phone_spans(text):
        out += [text[last:s], REDACTED]
        last = e
    out.append(text[last:])
    return "".join(out)
