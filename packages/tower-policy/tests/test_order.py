"""Named cases for rule order (03 §3): consent before facts; carrier error before staleness."""

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from tower_policy import (
    ConsentView,
    Facts,
    ReasonCode,
    Thresholds,
    evaluate_line,
    evaluate_reachability,
    within,
)

pytestmark = pytest.mark.unit

FactsBuilder = Callable[..., Facts]
ConsentBuilder = Callable[..., ConsentView]


def test_not_bound_with_a_swap_is_not_bound_never_the_swap(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    f = make_facts(
        sim_swapped=True, latest_sim_change=now - timedelta(minutes=5), call_forwarding="unconditional"
    )
    o = evaluate_line(f, make_consent(bound=False), now)
    assert o.kind == "refuse" and o.reason_codes == [ReasonCode.NOT_BOUND]


def test_no_grant_with_a_swap_is_no_consent(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    f = make_facts(sim_swapped=True, latest_sim_change=now - timedelta(minutes=5))
    assert evaluate_line(f, make_consent(grant="none"), now).reason_codes == [ReasonCode.NO_CONSENT]


def test_revoked_grant_is_no_consent_even_for_owner(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    f = make_facts(sim_swapped=True, latest_sim_change=now - timedelta(minutes=5))
    assert evaluate_line(f, make_consent(grant="owner", revoked=True), now).reason_codes == [
        ReasonCode.NO_CONSENT
    ]
    assert evaluate_reachability(f, make_consent(grant="owner", revoked=True), now).reason_codes == [
        ReasonCode.NO_CONSENT
    ]


def test_not_bound_beats_no_consent(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    f = make_facts(sim_swapped=False, call_forwarding="none")
    assert evaluate_line(f, make_consent(bound=False, grant="none"), now).reason_codes == [
        ReasonCode.NOT_BOUND
    ]


def test_consent_before_carrier_error(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    f = make_facts(sim_swapped=None, call_forwarding="unknown", reachable=None)
    assert evaluate_line(f, make_consent(bound=False), now).reason_codes == [ReasonCode.NOT_BOUND]
    assert evaluate_reachability(f, make_consent(grant="none"), now).reason_codes == [ReasonCode.NO_CONSENT]


def test_self_with_no_line_is_not_bound(make_facts: FactsBuilder, now: datetime) -> None:
    """04 §5: `"self"` with no Line resolves to `bound=False` → NOT_BOUND (the bind-link path)."""
    view = ConsentView(bound=False, grant="none")
    f = make_facts(sim_swapped=None, call_forwarding="unknown", reachable=None)
    assert evaluate_line(f, view, now).reason_codes == [ReasonCode.NOT_BOUND]
    assert evaluate_reachability(f, view, now).reason_codes == [ReasonCode.NOT_BOUND]


def test_alias_with_no_live_grant_is_no_consent(make_facts: FactsBuilder, now: datetime) -> None:
    """04 §5: an alias with no live Grant resolves to `bound=True, grant="none"` → NO_CONSENT, never NOT_BOUND."""
    view = ConsentView(bound=True, grant="none")
    f = make_facts(sim_swapped=None, call_forwarding="unknown", reachable=None)
    assert evaluate_line(f, view, now).reason_codes == [ReasonCode.NO_CONSENT]
    assert evaluate_reachability(f, view, now).reason_codes == [ReasonCode.NO_CONSENT]


def test_carrier_error_before_staleness_line(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    f = make_facts(stale=True, sim_swapped=None, call_forwarding="unknown")
    assert evaluate_line(f, make_consent(), now).reason_codes == [ReasonCode.CARRIER_ERROR]


def test_carrier_error_before_staleness_reachability(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    f = make_facts(stale=True, reachable=None)
    assert evaluate_reachability(f, make_consent(), now).reason_codes == [ReasonCode.CARRIER_ERROR]


def test_partial_carrier_answer_is_an_error(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    """D4: either fact unknown, with no known alarm, is a carrier error — a half answer is never OK."""
    for f in (
        make_facts(sim_swapped=None, call_forwarding="none"),
        make_facts(sim_swapped=None, call_forwarding="conditional"),
        make_facts(sim_swapped=False, call_forwarding="unknown"),
        make_facts(sim_swapped=True, latest_sim_change=now - timedelta(days=30), call_forwarding="unknown"),
    ):
        assert evaluate_line(f, make_consent(), now).reason_codes == [ReasonCode.CARRIER_ERROR], f


def test_partial_carrier_answer_never_hides_a_known_alarm(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    """D4: swap unknown + forwarding unconditional → CHANGED [CALL_FORWARDING_SET]; and the mirror case."""
    o = evaluate_line(make_facts(sim_swapped=None, call_forwarding="unconditional"), make_consent(), now)
    assert (o.kind, o.reason_codes) == ("changed", [ReasonCode.CALL_FORWARDING_SET])
    f = make_facts(sim_swapped=True, latest_sim_change=now - timedelta(hours=1), call_forwarding="unknown")
    o = evaluate_line(f, make_consent(), now)
    assert (o.kind, o.reason_codes) == ("changed", [ReasonCode.SIM_SWAPPED_RECENT])


def test_stale_before_facts(make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime) -> None:
    f = make_facts(stale=True, sim_swapped=True, latest_sim_change=now - timedelta(minutes=5))
    assert evaluate_line(f, make_consent(), now).reason_codes == [ReasonCode.STALE_DATA]
    f2 = make_facts(stale=True, reachable=False)
    assert evaluate_reachability(f2, make_consent(), now).reason_codes == [ReasonCode.STALE_DATA]


def test_exactly_stale_is_still_fresh(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime, thresholds: Thresholds
) -> None:
    f = Facts(fetched_at=now - thresholds.STALE, sim_swapped=False, call_forwarding="none")
    assert evaluate_line(f, make_consent(), now).kind == "ok"


def test_swap_without_a_date_counts_as_swapped(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime, thresholds: Thresholds
) -> None:
    assert within(None, now, thresholds.SWAP_WINDOW)
    f = make_facts(sim_swapped=True, latest_sim_change=None)
    assert evaluate_line(f, make_consent(), now).reason_codes == [ReasonCode.SIM_SWAPPED_RECENT]


def test_swap_outside_window_is_ok(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime, thresholds: Thresholds
) -> None:
    f = make_facts(
        sim_swapped=True,
        latest_sim_change=now - thresholds.SWAP_WINDOW - timedelta(seconds=1),
        call_forwarding="none",
    )
    assert evaluate_line(f, make_consent(), now).kind == "ok"
    f2 = make_facts(sim_swapped=True, latest_sim_change=now - thresholds.SWAP_WINDOW)
    assert evaluate_line(f2, make_consent(), now).reason_codes == [ReasonCode.SIM_SWAPPED_RECENT]


def test_both_changes_in_rule_order(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    f = make_facts(
        sim_swapped=True, latest_sim_change=now - timedelta(hours=2), call_forwarding="unconditional"
    )
    o = evaluate_line(f, make_consent(grant="watch"), now)
    assert o.kind == "changed"
    assert o.reason_codes == [ReasonCode.SIM_SWAPPED_RECENT, ReasonCode.CALL_FORWARDING_SET]


def test_conditional_forwarding_is_not_a_change(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    assert (
        evaluate_line(make_facts(sim_swapped=False, call_forwarding="conditional"), make_consent(), now).kind
        == "ok"
    )


def test_reachability_ignores_sim_and_forwarding(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    """Reachability answers only about reachability; swap facts are neither required nor reported."""
    f = make_facts(sim_swapped=None, call_forwarding="unknown", reachable=True)
    assert evaluate_reachability(f, make_consent(grant="reachability"), now).reason_codes == [ReasonCode.OK]
    f2 = make_facts(sim_swapped=True, latest_sim_change=now, reachable=False)
    assert evaluate_reachability(f2, make_consent(grant="reachability"), now).reason_codes == [
        ReasonCode.UNREACHABLE
    ]


def test_engine_does_not_distinguish_grant_levels(
    make_facts: FactsBuilder, make_consent: ConsentBuilder, now: datetime
) -> None:
    """Which tools a grant level allows is decided by Tower (02); the engine only knows 'some grant'."""
    f = make_facts(sim_swapped=True, latest_sim_change=now - timedelta(hours=1))
    for g in ("owner", "watch", "reachability"):
        assert evaluate_line(f, make_consent(grant=g), now).reason_codes == [ReasonCode.SIM_SWAPPED_RECENT]
