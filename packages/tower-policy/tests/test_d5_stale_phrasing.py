"""D5 (code-vs-docs.md): `STALE_DATA` must not say the line "was fine" when the last-known state showed a change.

03 §4: the stale template is neutral — it says when the last answer was, never what it meant. These rows are the
policy table's stale rows (every combination of last-known facts, both evaluators' code, both forms); the
wording must be the same whatever those facts held, so it can never call a swapped or forwarded line fine.
"""

from datetime import UTC, datetime, timedelta
from itertools import product

import pytest
from tower_policy import (
    ConsentView,
    Facts,
    ReasonCode,
    default_thresholds,
    evaluate_line,
    evaluate_reachability,
    phrase,
)
from tower_policy.phrasing import TEMPLATES

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 6, 13, 21, tzinfo=UTC)
TZ = "America/Toronto"
VERDICT_WORDS = ("fine", "as it was", "ok", "okay", "normal", "nothing", "safe")


def stale_facts(sim: str, fwd: str, reach: str) -> Facts:
    t = default_thresholds()
    latest = {"inside": NOW - timedelta(hours=1), "outside": NOW - t.SWAP_WINDOW - timedelta(hours=1)}.get(
        sim
    )
    return Facts(
        sim_swapped=sim != "false",
        latest_sim_change=latest,
        call_forwarding=fwd,  # type: ignore[arg-type]
        reachable=reach == "true",
        connectivity="DATA" if reach == "true" else "NONE",
        last_status_time=NOW - timedelta(minutes=40),
        fetched_at=NOW - t.STALE - timedelta(minutes=5),
    )


OWNER = ConsentView(bound=True, grant="owner")
ROWS = list(
    product(("inside", "outside", "false"), ("none", "unconditional", "conditional"), ("true", "false"))
)


def test_every_stale_row_is_refused_as_stale() -> None:
    for sim, fwd, reach in ROWS:
        f = stale_facts(sim, fwd, reach)
        assert evaluate_line(f, OWNER, NOW).reason_codes == [ReasonCode.STALE_DATA], (sim, fwd, reach)
        assert evaluate_reachability(f, OWNER, NOW).reason_codes == [ReasonCode.STALE_DATA], (sim, fwd, reach)


@pytest.mark.parametrize("form", ["voice", "sms"])
def test_stale_wording_carries_no_verdict(form: str) -> None:
    said = set()
    for sim, fwd, reach in ROWS:
        f = stale_facts(sim, fwd, reach)
        for outcome in (evaluate_line(f, OWNER, NOW), evaluate_reachability(f, OWNER, NOW)):
            text = phrase(outcome, f, alias=None, tz=TZ, form=form, now=NOW)  # type: ignore[arg-type]
            words = text.lower()
            assert not [w for w in VERDICT_WORDS if f" {w}" in f" {words.replace('.', ' ')}"], (
                sim,
                fwd,
                text,
            )
            assert "9:" in text  # the time of the last answer (fetched_at, 9:06 in Toronto) is said
            said.add(text)
    assert len(said) == 1, said  # identical whatever the last-known facts held


def test_stale_template_is_the_neutral_one_in_03() -> None:
    assert (
        TEMPLATES[ReasonCode.STALE_DATA]["voice"]
        == "I can't reach your carrier right now. The last I heard was at {time}."
    )
    assert (
        TEMPLATES[ReasonCode.STALE_DATA]["sms"] == "Can't reach your carrier right now. Last heard at {time}."
    )
