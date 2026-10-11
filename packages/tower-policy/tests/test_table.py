"""The decision table — the full cross-product from 03 §6, for both evaluators.

Asserts every row returns and the properties that make the design true (consent before facts, no
leak of a swap on an unconsented line, stale/error precedence). When `POLICY_TABLE_OUT` is set, the
rendered markdown is written there (`make policy-table` → `artifacts/policy-table.md`).
"""

import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path

import pytest
from tower_policy import (
    ConsentView,
    Facts,
    Outcome,
    ReasonCode,
    Thresholds,
    evaluate_line,
    evaluate_reachability,
    policy_version,
)
from tower_policy.phrasing import TEMPLATES

pytestmark = pytest.mark.unit

BOUND = (True, False)
GRANT = ("owner", "watch", "reachability", "none", "revoked")
SIM = ("inside", "outside", "false", "none")
FWD = ("none", "unconditional", "conditional", "unknown")
REACH = ("true", "false", "none")
AGE = ("fresh", "stale")


@dataclass(frozen=True)
class Row:
    bound: bool
    grant: str
    sim: str
    fwd: str
    reach: str
    age: str
    line: Outcome
    reachability: Outcome

    @property
    def consented(self) -> bool:
        return self.bound and self.grant not in ("none", "revoked")


def build_facts(sim: str, fwd: str, reach: str, age: str, now: datetime, t: Thresholds) -> Facts:
    fetched = now - (t.STALE + timedelta(seconds=1) if age == "stale" else timedelta(seconds=5))
    sim_swapped: bool | None = None if sim == "none" else sim != "false"
    latest: datetime | None = None
    if sim == "inside":
        latest = now - timedelta(hours=1)
    elif sim == "outside":
        latest = now - (t.SWAP_WINDOW + timedelta(hours=1))
    reachable: bool | None = None if reach == "none" else reach == "true"
    return Facts(
        fetched_at=fetched,
        sim_swapped=sim_swapped,
        latest_sim_change=latest,
        call_forwarding=fwd,
        reachable=reachable,
        connectivity="DATA" if reachable else ("NONE" if reachable is False else "UNKNOWN"),
        last_status_time=fetched if reachable is not None else None,
    )


def build_consent(bound: bool, grant: str, now: datetime) -> ConsentView:
    if grant == "revoked":
        return ConsentView(bound=bound, grant="owner", revoked_at=now - timedelta(hours=1))
    return ConsentView(bound=bound, grant=grant, revoked_at=None)


def cross_product(now: datetime, t: Thresholds) -> list[Row]:
    rows: list[Row] = []
    for bound, grant, sim, fwd, reach, age in product(BOUND, GRANT, SIM, FWD, REACH, AGE):
        f = build_facts(sim, fwd, reach, age, now, t)
        c = build_consent(bound, grant, now)
        rows.append(
            Row(
                bound,
                grant,
                sim,
                fwd,
                reach,
                age,
                evaluate_line(f, c, now, t),
                evaluate_reachability(f, c, now, t),
            )
        )
    return rows


def expected_line(r: Row) -> list[ReasonCode]:
    if not r.bound:
        return [ReasonCode.NOT_BOUND]
    if r.grant in ("none", "revoked"):
        return [ReasonCode.NO_CONSENT]
    codes = []
    if r.sim == "inside":
        codes.append(ReasonCode.SIM_SWAPPED_RECENT)
    if r.fwd == "unconditional":
        codes.append(ReasonCode.CALL_FORWARDING_SET)
    if _partial(r) and not codes:  # D4: one unanswered fact is a carrier error, unless the other one alarms
        return [ReasonCode.CARRIER_ERROR]
    if r.age == "stale":
        return [ReasonCode.STALE_DATA]
    return codes or [ReasonCode.OK]


def _partial(r: Row) -> bool:
    """At least one of the two line facts is unknown (the carrier did not answer that call)."""
    return r.sim == "none" or r.fwd == "unknown"


def _alarming(r: Row) -> bool:
    return r.sim == "inside" or r.fwd == "unconditional"


def expected_reach(r: Row) -> list[ReasonCode]:
    if not r.bound:
        return [ReasonCode.NOT_BOUND]
    if r.grant in ("none", "revoked"):
        return [ReasonCode.NO_CONSENT]
    if r.reach == "none":
        return [ReasonCode.CARRIER_ERROR]
    if r.age == "stale":
        return [ReasonCode.STALE_DATA]
    return [ReasonCode.UNREACHABLE] if r.reach == "false" else [ReasonCode.OK]


@pytest.fixture(scope="module")
def rows(now: datetime, thresholds: Thresholds) -> list[Row]:
    return cross_product(now, thresholds)


def test_every_row_returns_an_outcome(rows: list[Row]) -> None:
    assert len(rows) == 2 * 5 * 4 * 4 * 3 * 2 == 960
    for r in rows:
        assert r.line.kind in ("ok", "changed", "refuse")
        assert r.reachability.kind in ("ok", "changed", "refuse")
        assert r.line.reason_codes and r.reachability.reason_codes


def test_table_matches_the_rules_as_written(rows: list[Row]) -> None:
    for r in rows:
        assert r.line.reason_codes == expected_line(r), r
        assert r.reachability.reason_codes == expected_reach(r), r


def test_kind_is_consistent_with_codes(rows: list[Row]) -> None:
    for r in rows:
        for o in (r.line, r.reachability):
            if o.kind == "ok":
                assert o.reason_codes == [ReasonCode.OK]
            elif o.kind == "refuse":
                assert len(o.reason_codes) == 1
                assert o.reason_codes[0] in {
                    ReasonCode.NOT_BOUND,
                    ReasonCode.NO_CONSENT,
                    ReasonCode.STALE_DATA,
                    ReasonCode.CARRIER_ERROR,
                }
            else:
                assert o.reason_codes
                assert set(o.reason_codes) <= {
                    ReasonCode.SIM_SWAPPED_RECENT,
                    ReasonCode.CALL_FORWARDING_SET,
                    ReasonCode.UNREACHABLE,
                }


def test_unbound_never_leaks_a_swap(rows: list[Row]) -> None:
    """The leak-prevention case: bound=False with a swap inside the window is NOT_BOUND, nothing else."""
    leak = [r for r in rows if not r.bound and r.sim == "inside"]
    assert leak, "the cross-product must contain the leak-prevention case"
    for r in leak:
        assert r.line.reason_codes == [ReasonCode.NOT_BOUND]
        assert r.reachability.reason_codes == [ReasonCode.NOT_BOUND]
        assert ReasonCode.SIM_SWAPPED_RECENT not in r.line.reason_codes


def test_unconsented_never_leaks_anything(rows: list[Row]) -> None:
    for r in rows:
        if not r.consented:
            assert r.line.kind == "refuse" and r.reachability.kind == "refuse"
            assert r.line.reason_codes[0] in (ReasonCode.NOT_BOUND, ReasonCode.NO_CONSENT)
            assert r.reachability.reason_codes[0] in (ReasonCode.NOT_BOUND, ReasonCode.NO_CONSENT)


def test_swap_inside_window_is_always_reported_when_allowed(rows: list[Row]) -> None:
    for r in rows:
        if r.consented and r.sim == "inside" and r.age == "fresh":
            assert ReasonCode.SIM_SWAPPED_RECENT in r.line.reason_codes
        if r.consented and r.sim == "outside":
            assert ReasonCode.SIM_SWAPPED_RECENT not in r.line.reason_codes


def test_carrier_error_beats_staleness(rows: list[Row]) -> None:
    for r in rows:
        if r.consented and r.age == "stale":
            if _partial(r) and not _alarming(r):
                assert r.line.reason_codes == [ReasonCode.CARRIER_ERROR]
            else:
                assert r.line.reason_codes == [ReasonCode.STALE_DATA]
            if r.reach == "none":
                assert r.reachability.reason_codes == [ReasonCode.CARRIER_ERROR]
            else:
                assert r.reachability.reason_codes == [ReasonCode.STALE_DATA]


def test_partial_answer_is_never_ok(rows: list[Row]) -> None:
    """D4: one carrier call failing never yields "Your line is as it was."."""
    partial = [
        r for r in rows if r.consented and _partial(r) and not (r.sim == "none" and r.fwd == "unknown")
    ]
    assert partial, "the cross-product must contain one-fact-unknown rows"
    for r in rows:
        if _partial(r):
            assert r.line.kind != "ok", r


def test_partial_answer_never_hides_a_known_alarm(rows: list[Row]) -> None:
    """D4: the fact that *is* known is reported when it alarms, even though the other one is unknown."""
    for r in rows:
        if r.consented and r.age == "fresh" and _partial(r):
            if r.fwd == "unconditional":
                assert ReasonCode.CALL_FORWARDING_SET in r.line.reason_codes, r
            if r.sim == "inside":
                assert ReasonCode.SIM_SWAPPED_RECENT in r.line.reason_codes, r


def test_reachability_reports_the_instant_fact_only(rows: list[Row]) -> None:
    for r in rows:
        if r.consented and r.age == "fresh" and r.reach != "none":
            assert r.reachability.reason_codes == (
                [ReasonCode.UNREACHABLE] if r.reach == "false" else [ReasonCode.OK]
            )
            # the line evaluator never says UNREACHABLE; the reachability evaluator never says swap/forwarding
            assert ReasonCode.UNREACHABLE not in r.line.reason_codes
            assert not {ReasonCode.SIM_SWAPPED_RECENT, ReasonCode.CALL_FORWARDING_SET} & set(
                r.reachability.reason_codes
            )


# --- rendering ---------------------------------------------------------------------------------


def _fmt(o: Outcome) -> str:
    return f"{o.kind} · {', '.join(o.reason_codes)}"


def render_markdown(rows: list[Row], t: Thresholds) -> str:
    sim_label = {"inside": "true (inside)", "outside": "true (outside)", "false": "false", "none": "None"}
    reach_label = {"true": "true", "false": "false", "none": "None"}
    by_line = Counter(_fmt(r.line) for r in rows)
    by_reach = Counter(_fmt(r.reachability) for r in rows)
    out: list[str] = [
        "# Policy decision table",
        "",
        "Generated by `make policy-table` from `packages/tower-policy/tests/test_table.py` — the full",
        "cross-product of `docs/architecture/components/03-policy-engine.md` §6, evaluated by",
        "`evaluate_line` (line_is_ok) and `evaluate_reachability` (is_reachable). Same inputs → same row, always.",
        "",
        f"- policy_version: `{policy_version()}`",
        f"- SWAP_WINDOW = {t.SWAP_WINDOW}, STALE = {t.STALE}",
        f"- rows: {len(rows)} (bound × grant × sim_swapped × call_forwarding × reachable × fetched_at)",
        "- `grant = revoked` means `grant = owner` with `revoked_at` set.",
        "- `sim_swapped = true (inside)` means `latest_sim_change` is inside SWAP_WINDOW; `(outside)` is past it.",
        "",
        "## Summary",
        "",
        "| line_is_ok outcome | rows | is_reachable outcome | rows |",
        "|---|---:|---|---:|",
    ]
    lk = sorted(by_line.items(), key=lambda kv: (-kv[1], kv[0]))
    rk = sorted(by_reach.items(), key=lambda kv: (-kv[1], kv[0]))
    for i in range(max(len(lk), len(rk))):
        left = f"`{lk[i][0]}` | {lk[i][1]}" if i < len(lk) else " | "
        right = f"`{rk[i][0]}` | {rk[i][1]}" if i < len(rk) else " | "
        out.append(f"| {left} | {right} |")
    out += [
        "",
        "## The leak-prevention row",
        "",
        "An unbound line with a SIM swap inside the window is refused as `NOT_BOUND` — the swap is never mentioned:",
        "",
        "| bound | grant | sim_swapped | call_forwarding | reachable | fetched_at | line_is_ok | is_reachable |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if not r.bound and r.sim == "inside" and r.grant == "owner" and r.fwd == "none" and r.reach == "true":
            out.append(
                f"| {r.bound} | {r.grant} | {sim_label[r.sim]} | {r.fwd} | {reach_label[r.reach]} | {r.age} "
                f"| `{_fmt(r.line)}` | `{_fmt(r.reachability)}` |"
            )
    stale = [r for r in rows if r.line.reason_codes == [ReasonCode.STALE_DATA]]
    out += [
        "",
        "## STALE_DATA wording",
        "",
        f"All {len(stale)} `refuse · STALE_DATA` rows above and below use one neutral sentence, whatever the "
        "last-known facts held — a swap or forwarding included (03 §4; code-vs-docs D5). `{time}` is when the last "
        "answer was fetched:",
        "",
        f"- voice: “{TEMPLATES[ReasonCode.STALE_DATA]['voice']}”",
        f"- sms: “{TEMPLATES[ReasonCode.STALE_DATA]['sms']}”",
    ]
    header = [
        "",
        "| bound | grant | sim_swapped | call_forwarding | reachable | fetched_at | line_is_ok | is_reachable |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for kind, title in (("refuse", "Refused"), ("changed", "Changed"), ("ok", "OK")):
        group = [r for r in rows if r.line.kind == kind]
        out += ["", f"## line_is_ok → {title} ({len(group)} rows)", *header]
        for r in group:
            out.append(
                f"| {r.bound} | {r.grant} | {sim_label[r.sim]} | {r.fwd} | {reach_label[r.reach]} | {r.age} "
                f"| `{_fmt(r.line)}` | `{_fmt(r.reachability)}` |"
            )
    return "\n".join(out) + "\n"


def test_render_table(rows: list[Row], thresholds: Thresholds) -> None:
    md = render_markdown(rows, thresholds)
    assert "| False | owner | true (inside) | none | true | fresh | `refuse · NOT_BOUND`" in md
    assert md.count("\n| ") >= len(rows)
    target = os.environ.get("POLICY_TABLE_OUT")
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md, encoding="utf-8")
        print(f"\nwrote {path} ({len(rows)} rows)")
