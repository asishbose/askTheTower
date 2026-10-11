"""Templates: every code × both forms renders; no phone-number-like digits; no health words;
alias substituted; SMS ≤ 160 chars; times rendered in the line-holder's zone."""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import product

import pytest
from tower_policy import (
    AUDIT_ONLY_CODES,
    TEMPLATES,
    Facts,
    Outcome,
    ReasonCode,
    phrase,
    phrase_code,
    policy_version,
    render_time,
)

from tests.privacy.patterns import E164_STRICT, HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.unit

FactsBuilder = Callable[..., Facts]
FORMS = ("voice", "sms")
ALIASES = (None, "Mom", "Amma", "Grandpa Joe")
# An alias chosen at consent time may itself be anything; the templates must not add digits.
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _outcome_for(code: ReasonCode) -> Outcome:
    if code is ReasonCode.OK:
        return Outcome(kind="ok", reason_codes=[code])
    if code in (ReasonCode.SIM_SWAPPED_RECENT, ReasonCode.CALL_FORWARDING_SET, ReasonCode.UNREACHABLE):
        return Outcome(kind="changed", reason_codes=[code])
    return Outcome(kind="refuse", reason_codes=[code])


def test_templates_cover_exactly_the_twelve_codes() -> None:
    assert set(TEMPLATES) == set(ReasonCode)
    assert len(ReasonCode) == 12
    for code, forms in TEMPLATES.items():
        assert set(forms) == {"voice", "sms"}
        if code in AUDIT_ONLY_CODES:
            assert forms["voice"] == "" and forms["sms"] == ""
        else:
            assert forms["voice"] and forms["sms"]


def test_templates_only_interpolate_time_and_name() -> None:
    for forms in TEMPLATES.values():
        for text in forms.values():
            assert set(PLACEHOLDER.findall(text)) <= {"time", "Name"}


def test_voice_templates_match_the_design_doc() -> None:
    assert TEMPLATES[ReasonCode.OK]["voice"] == "Your line is as it was."
    assert (
        TEMPLATES[ReasonCode.NOT_BOUND]["voice"]
        == "I need to connect your line first — I'll send you a link."
    )
    assert TEMPLATES[ReasonCode.NO_CONSENT]["voice"] == "{Name} hasn't shared that with you."
    assert (
        TEMPLATES[ReasonCode.CARRIER_ERROR]["voice"]
        == "I can't reach your carrier right now. Try again in a minute."
    )
    assert "off the network since {time}" in TEMPLATES[ReasonCode.UNREACHABLE]["voice"]


@pytest.mark.parametrize(("code", "form"), list(product(list(ReasonCode), FORMS)))
def test_every_code_renders_in_both_forms(
    code: ReasonCode, form: str, make_facts: FactsBuilder, now: datetime
) -> None:
    f = make_facts(
        sim_swapped=True, latest_sim_change=now - timedelta(minutes=16), reachable=False, last_status_time=now
    )
    for alias in ALIASES:
        text = phrase(_outcome_for(code), f, alias=alias, tz="America/Toronto", form=form, now=now)
        assert isinstance(text, str)
        if code in AUDIT_ONLY_CODES:
            assert text == ""
        else:
            assert text and "{" not in text and "}" not in text
        assert not E164_STRICT.search(text), text
        assert not phone_hits(text)
        assert not HEALTH_WORDS.search(text), text
        if form == "sms":
            assert len(text) <= 160, (len(text), text)


def test_alias_is_substituted(make_facts: FactsBuilder, now: datetime) -> None:
    f = make_facts(reachable=False, last_status_time=now - timedelta(hours=3))
    unreachable = Outcome(kind="changed", reason_codes=[ReasonCode.UNREACHABLE])
    assert phrase(unreachable, f, alias="Mom", tz="UTC", form="voice").startswith("Mom's phone has been off")
    assert phrase(unreachable, f, alias=None, tz="UTC", form="voice").startswith("That person's phone")
    no_consent = Outcome(kind="refuse", reason_codes=[ReasonCode.NO_CONSENT])
    assert phrase(no_consent, f, alias="Amma", tz="UTC", form="sms") == "Amma hasn't shared that with you."


def test_acceptance_sim_swapped_at_1414z_in_toronto_is_1014_today(make_facts: FactsBuilder) -> None:
    now = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
    f = Facts(fetched_at=now, sim_swapped=True, latest_sim_change=datetime(2026, 10, 6, 14, 14, tzinfo=UTC))
    o = Outcome(kind="changed", reason_codes=[ReasonCode.SIM_SWAPPED_RECENT])
    text = phrase(o, f, alias=None, tz="America/Toronto", form="voice", now=now)
    assert "10:14" in text and "today" in text
    assert (
        text
        == "Your SIM was moved to another device at 10:14 today. If that wasn't you, call your carrier now."
    )
    # `now` defaults to fetched_at, so the same call without `now` says the same thing
    assert phrase(o, f, alias=None, tz="America/Toronto", form="voice") == text


def test_time_rendering_forms() -> None:
    now = datetime(2026, 10, 6, 20, 0, tzinfo=UTC)  # 16:00 in Toronto, Tuesday
    tz = "America/Toronto"
    assert render_time(datetime(2026, 10, 6, 18, 14, tzinfo=UTC), now, tz) == "2:14 today"
    assert render_time(datetime(2026, 10, 6, 4, 5, tzinfo=UTC), now, tz) == "12:05 today"  # 00:05 local
    assert render_time(datetime(2026, 10, 5, 13, 30, tzinfo=UTC), now, tz) == "yesterday at 9:30"
    # 03:30Z on Oct 6 is 23:30 on Oct 5 in Toronto: "yesterday", not "today"
    assert render_time(datetime(2026, 10, 6, 3, 30, tzinfo=UTC), now, tz) == "yesterday at 11:30"
    assert render_time(datetime(2026, 10, 3, 13, 30, tzinfo=UTC), now, tz) == "on Saturday at 9:30"
    assert render_time(datetime(2026, 9, 20, 13, 30, tzinfo=UTC), now, tz) == "on Sep 20 at 9:30"
    # 20:00Z is already 01:30 on Oct 7 in Kolkata, so 12:00Z (17:30 local, Oct 6) is "yesterday" there
    assert render_time(datetime(2026, 10, 6, 12, 0, tzinfo=UTC), now, "Asia/Kolkata") == "yesterday at 5:30"
    assert (
        render_time(
            datetime(2026, 10, 6, 12, 0, tzinfo=UTC), datetime(2026, 10, 6, 13, 0, tzinfo=UTC), "Asia/Kolkata"
        )
        == "5:30 today"
    )


def test_changed_with_two_codes_joins_two_sentences(make_facts: FactsBuilder, now: datetime) -> None:
    f = make_facts(
        sim_swapped=True, latest_sim_change=now - timedelta(minutes=16), call_forwarding="unconditional"
    )
    o = Outcome(kind="changed", reason_codes=[ReasonCode.SIM_SWAPPED_RECENT, ReasonCode.CALL_FORWARDING_SET])
    for form in FORMS:
        text = phrase(o, f, alias=None, tz="Europe/London", form=form)
        one = phrase_code(ReasonCode.SIM_SWAPPED_RECENT, f, alias=None, tz="Europe/London", form=form)
        two = phrase_code(ReasonCode.CALL_FORWARDING_SET, f, alias=None, tz="Europe/London", form=form)
        assert text == f"{one} {two}"
        assert text.count(". ") >= 2
    sms = phrase(o, f, alias=None, tz="Europe/London", form="sms", now=now - timedelta(days=20))
    assert len(sms) <= 160, (len(sms), sms)


def test_ok_is_the_single_sentence(make_facts: FactsBuilder) -> None:
    o = Outcome(kind="ok", reason_codes=[ReasonCode.OK])
    assert phrase(o, make_facts(), alias="Mom", tz="UTC", form="voice") == "Your line is as it was."


def test_stale_uses_fetched_at(make_facts: FactsBuilder, now: datetime) -> None:
    f = make_facts(stale=True)
    o = Outcome(kind="refuse", reason_codes=[ReasonCode.STALE_DATA])
    expected = render_time(f.fetched_at, now, "UTC")
    assert expected in phrase(o, f, alias=None, tz="UTC", form="voice", now=now)


def test_policy_version_is_stable_sha256() -> None:
    v = policy_version()
    assert re.fullmatch(r"[0-9a-f]{64}", v)
    assert policy_version() == v
