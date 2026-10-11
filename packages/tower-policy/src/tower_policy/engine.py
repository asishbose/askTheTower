"""The rules, transcribed from `docs/architecture/components/03-policy-engine.md` §3, in that order.

Pure: no I/O, no clock, no randomness. `now` is an argument. Same inputs → same outcome, always.

Order matters: consent before facts, so an unbound or unconsented line never leaks whether a swap
happened; carrier errors before staleness, so an outage is reported as an outage.
"""

from datetime import datetime, timedelta

from tower_policy.codes import ReasonCode
from tower_policy.thresholds import Thresholds, default_thresholds
from tower_policy.types import ConsentView, Facts, Outcome, changed, ok, refuse


def within(when: datetime | None, now: datetime, window: timedelta) -> bool:
    """True if `when` is no older than `window` before `now`.

    `None` counts as inside: the SIM Swap API may say "swapped" without a date, and the safe
    reading of "swapped, date unknown" is "recent".
    """
    if when is None:
        return True
    return (now - when) <= window


def _consent_gate(c: ConsentView) -> Outcome | None:
    if not c.bound:
        return refuse(ReasonCode.NOT_BOUND)
    if c.grant == "none" or c.revoked_at is not None:
        return refuse(ReasonCode.NO_CONSENT)
    return None


def evaluate_line(
    facts: Facts, consent: ConsentView, now: datetime, thresholds: Thresholds | None = None
) -> Outcome:
    """`line_is_ok`: SIM swap and call forwarding, gated by consent, carrier error, staleness."""
    t = thresholds if thresholds is not None else default_thresholds()
    gate = _consent_gate(consent)
    if gate is not None:
        return gate
    codes: list[ReasonCode] = []
    if facts.sim_swapped and within(facts.latest_sim_change, now, t.SWAP_WINDOW):
        codes.append(ReasonCode.SIM_SWAPPED_RECENT)
    if facts.call_forwarding == "unconditional":
        codes.append(ReasonCode.CALL_FORWARDING_SET)
    # Any fact the carrier did not answer is a carrier error ("never guess", 02 §6): a half answer is never
    # "as it was". The one exception never hides an alarm: if the fact that *is* known is alarming, say so.
    unknown = facts.sim_swapped is None or facts.call_forwarding == "unknown"
    if unknown and not codes:
        return refuse(ReasonCode.CARRIER_ERROR)
    if (now - facts.fetched_at) > t.STALE:
        return refuse(ReasonCode.STALE_DATA)
    return ok() if not codes else changed(codes)


def evaluate_reachability(
    facts: Facts, consent: ConsentView, now: datetime, thresholds: Thresholds | None = None
) -> Outcome:
    """`is_reachable`: the instant fact only. The 20-min / 4-h windows live in Alerts (06 §2).

    Same consent gates as `evaluate_line`; the carrier-error gate for this tool is
    `reachable is None`, checked before staleness (carrier error before staleness, as in 03 §3).
    """
    t = thresholds if thresholds is not None else default_thresholds()
    gate = _consent_gate(consent)
    if gate is not None:
        return gate
    if facts.reachable is None:
        return refuse(ReasonCode.CARRIER_ERROR)
    if (now - facts.fetched_at) > t.STALE:
        return refuse(ReasonCode.STALE_DATA)
    return ok() if facts.reachable else changed([ReasonCode.UNREACHABLE])
