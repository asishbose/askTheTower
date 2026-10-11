"""D4 (code-vs-docs.md): one carrier call failing must never yield `OK`.

02 §6: "Carrier timeout → STALE_DATA with last-known facts if a Watch exists, else CARRIER_ERROR" ("never guess").
The engine has no Watch; the tool substitutes last-known facts before calling it. So, at the engine, a fact the
carrier did not answer is a carrier error. `sim_swapped` is the fraud signal: unknown is never "as it was".
"""

from datetime import datetime

import pytest
from tower_policy import ReasonCode, Thresholds, evaluate_line

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("fwd", ["none", "conditional"])
def test_d4_sim_swap_unknown_with_quiet_forwarding_is_carrier_error(
    make_facts, make_consent, now: datetime, thresholds: Thresholds, fwd: str
) -> None:
    f = make_facts(sim_swapped=None, call_forwarding=fwd)
    out = evaluate_line(f, make_consent(), now, thresholds)
    assert out.kind != "ok", out
    assert out.reason_codes == [ReasonCode.CARRIER_ERROR]


def test_d4_sim_swap_unknown_beats_staleness(
    make_facts, make_consent, now: datetime, thresholds: Thresholds
) -> None:
    """Carrier error before staleness (03 §3 order) applies to a half answer too."""
    f = make_facts(stale=True, sim_swapped=None, call_forwarding="none")
    assert evaluate_line(f, make_consent(), now, thresholds).reason_codes == [ReasonCode.CARRIER_ERROR]


def test_d4_forwarding_unknown_with_no_swap_is_not_ok(
    make_facts, make_consent, now: datetime, thresholds: Thresholds
) -> None:
    """The mirror case: the forwarding call failed, the swap check said no. Still not "as it was"."""
    f = make_facts(sim_swapped=False, call_forwarding="unknown")
    out = evaluate_line(f, make_consent(), now, thresholds)
    assert out.kind != "ok", out
    assert out.reason_codes == [ReasonCode.CARRIER_ERROR]


def test_d4_consent_still_gates_first(
    make_facts, make_consent, now: datetime, thresholds: Thresholds
) -> None:
    f = make_facts(sim_swapped=None, call_forwarding="none")
    assert evaluate_line(f, make_consent(bound=False), now, thresholds).reason_codes == [ReasonCode.NOT_BOUND]
    assert evaluate_line(f, make_consent(grant="none"), now, thresholds).reason_codes == [
        ReasonCode.NO_CONSENT
    ]
