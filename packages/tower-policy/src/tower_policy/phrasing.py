"""Spoken and SMS templates keyed on `ReasonCode` (03 §4), and `phrase()` to render an outcome.

Only `{time}` and `{Name}` are ever interpolated. Templates never contain a phone number or a
health word; `tests/test_phrasing.py` asserts both with the shared regexes in `tests/privacy/`.
"""

from datetime import datetime
from typing import Final, Literal
from zoneinfo import ZoneInfo

from tower_policy.codes import ReasonCode
from tower_policy.types import Facts, Outcome

Form = Literal["voice", "sms"]

DEFAULT_NAME: Final = "That person"
SMS_MAX: Final = 160

# code → {form → template}. Audit-only codes carry no message (empty string in both forms).
TEMPLATES: Final[dict[ReasonCode, dict[Form, str]]] = {
    ReasonCode.OK: {
        "voice": "Your line is as it was.",
        "sms": "Your line is as it was.",
    },
    ReasonCode.SIM_SWAPPED_RECENT: {
        "voice": "Your SIM was moved to another device at {time}. If that wasn't you, call your carrier now.",
        "sms": "SIM moved to another device at {time}. Not you? Call your carrier now.",
    },
    ReasonCode.CALL_FORWARDING_SET: {
        "voice": "All your calls have been forwarding since {time}. If you didn't set that, call your carrier.",
        "sms": "All calls forwarding since {time}. Not you? Call your carrier.",
    },
    ReasonCode.UNREACHABLE: {
        "voice": "{Name}'s phone has been off the network since {time} — that's the network, nothing more.",
        "sms": "{Name}'s phone has been off the network since {time} - that's the network, nothing more.",
    },
    ReasonCode.NOT_BOUND: {
        "voice": "I need to connect your line first — I'll send you a link.",
        "sms": "Connect your line first - link to follow.",
    },
    ReasonCode.NO_CONSENT: {
        "voice": "{Name} hasn't shared that with you.",
        "sms": "{Name} hasn't shared that with you.",
    },
    # Neutral on purpose (D5): the last-known state may itself show a swap or forwarding, so the sentence says
    # when the last answer was, never what it meant. The facts (stale=true, as_of) carry the state itself.
    ReasonCode.STALE_DATA: {
        "voice": "I can't reach your carrier right now. The last I heard was at {time}.",
        "sms": "Can't reach your carrier right now. Last heard at {time}.",
    },
    ReasonCode.CARRIER_ERROR: {
        "voice": "I can't reach your carrier right now. Try again in a minute.",
        "sms": "Can't reach your carrier right now. Try again in a minute.",
    },
    ReasonCode.SERVICE_UNAVAILABLE: {
        "voice": "Something on my side isn't available. Try again in a minute.",
        "sms": "Something on our side isn't available. Try again in a minute.",
    },
    ReasonCode.SUPPRESSED_REVOKED: {"voice": "", "sms": ""},
    ReasonCode.ALERT_FAILED: {"voice": "", "sms": ""},
    ReasonCode.ACK_IGNORED_SWAPPED_LINE: {"voice": "", "sms": ""},
}

_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WEEKDAYS: Final = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def render_time(when: datetime, now: datetime, tz: str) -> str:
    """'2:14 today' | 'yesterday at 9:30' | 'on Monday at 9:30' | 'on Oct 3 at 9:30', in `tz`.

    Clock times are 12-hour without a leading zero, as the design doc writes them. The day is
    judged relative to `now` in the same zone.
    """
    zone = ZoneInfo(tz)
    w = when.astimezone(zone)
    n = now.astimezone(zone)
    hour = w.hour % 12 or 12
    clock = f"{hour}:{w.minute:02d}"
    days = (n.date() - w.date()).days
    if days == 0:
        return f"{clock} today"
    if days == 1:
        return f"yesterday at {clock}"
    if 1 < days < 7:
        return f"on {_WEEKDAYS[w.weekday()]} at {clock}"
    return f"on {_MONTHS[w.month - 1]} {w.day} at {clock}"


def _time_for(code: ReasonCode, facts: Facts) -> datetime:
    """Which timestamp a template's `{time}` refers to; always falls back to `fetched_at`."""
    if code is ReasonCode.SIM_SWAPPED_RECENT and facts.latest_sim_change is not None:
        return facts.latest_sim_change
    if code is ReasonCode.UNREACHABLE and facts.last_status_time is not None:
        return facts.last_status_time
    # CALL_FORWARDING_SET: the API reports no start time; "since" is when we observed it.
    # STALE_DATA: the last time we saw the line.
    return facts.fetched_at


def phrase_code(
    code: ReasonCode,
    facts: Facts,
    *,
    alias: str | None,
    tz: str,
    form: Form,
    now: datetime | None = None,
) -> str:
    """Render one code. Audit-only codes render as ''."""
    template = TEMPLATES[code][form]
    if not template:
        return ""
    at = now if now is not None else facts.fetched_at
    return template.format(time=render_time(_time_for(code, facts), at, tz), Name=alias or DEFAULT_NAME)


def phrase(
    outcome: Outcome,
    facts: Facts,
    *,
    alias: str | None,
    tz: str,
    form: Form,
    now: datetime | None = None,
) -> str:
    """Render an outcome: one sentence for `ok` and `refuse`; `changed` joins one sentence per code.

    `tz` is the line-holder's IANA zone; `alias` is the name chosen at consent time (`{Name}`).
    `now` fixes what "today" means and defaults to `facts.fetched_at` — the engine has no clock.
    """
    parts = [phrase_code(c, facts, alias=alias, tz=tz, form=form, now=now) for c in outcome.reason_codes]
    return " ".join(p for p in parts if p)
