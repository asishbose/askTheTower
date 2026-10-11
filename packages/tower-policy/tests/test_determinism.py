"""Same inputs → same outcome, always. 1,000 random valid inputs evaluated twice; and the package
source has no clock, randomness, environment or network — a grep asserts it."""

import random
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import tower_policy
from hypothesis import given, settings
from hypothesis import strategies as st
from tower_policy import ConsentView, Facts, default_thresholds, evaluate_line, evaluate_reachability, phrase

pytestmark = pytest.mark.unit

SRC = Path(tower_policy.__file__).parent

BASE = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

aware_dt = st.datetimes(
    min_value=datetime(2026, 1, 1), max_value=datetime(2026, 12, 31), timezones=st.just(UTC)
)
facts_st = st.builds(
    Facts,
    fetched_at=aware_dt,
    sim_swapped=st.none() | st.booleans(),
    latest_sim_change=st.none() | aware_dt,
    call_forwarding=st.sampled_from(["none", "unconditional", "conditional", "unknown"]),
    reachable=st.none() | st.booleans(),
    connectivity=st.sampled_from(["DATA", "SMS", "NONE", "UNKNOWN"]),
    last_status_time=st.none() | aware_dt,
)
consent_st = st.builds(
    ConsentView,
    bound=st.booleans(),
    grant=st.sampled_from(["owner", "watch", "reachability", "none"]),
    revoked_at=st.none() | aware_dt,
)
now_st = st.integers(min_value=-60 * 24 * 60, max_value=60 * 24 * 60).map(
    lambda m: BASE + timedelta(minutes=m)
)


def _random_inputs(seed: int, n: int) -> list[tuple[Facts, ConsentView, datetime]]:
    """n random *valid* inputs from a seeded generator (randomness lives in the test, never in src/)."""
    rng = random.Random(seed)  # noqa: S311 - test data, not crypto

    def dt() -> datetime:
        return BASE + timedelta(minutes=rng.randint(-90 * 24 * 60, 90 * 24 * 60))

    def maybe(v: object) -> object:
        return None if rng.random() < 0.25 else v

    out = []
    for _ in range(n):
        f = Facts(
            fetched_at=dt(),
            sim_swapped=maybe(rng.random() < 0.5),
            latest_sim_change=maybe(dt()),
            call_forwarding=rng.choice(["none", "unconditional", "conditional", "unknown"]),
            reachable=maybe(rng.random() < 0.5),
            connectivity=rng.choice(["DATA", "SMS", "NONE", "UNKNOWN"]),
            last_status_time=maybe(dt()),
        )
        c = ConsentView(
            bound=rng.random() < 0.7,
            grant=rng.choice(["owner", "watch", "reachability", "none"]),
            revoked_at=maybe(dt()),
        )
        out.append((f, c, dt()))
    return out


def test_1000_random_inputs_evaluated_twice_are_identical() -> None:
    t = default_thresholds()
    cases = _random_inputs(seed=20261006, n=1000)
    assert len(cases) == 1000
    first = [(evaluate_line(f, c, n, t), evaluate_reachability(f, c, n, t)) for f, c, n in cases]
    second = [(evaluate_line(f, c, n, t), evaluate_reachability(f, c, n, t)) for f, c, n in cases]
    assert first == second
    # a fresh generator with the same seed reproduces the same inputs and the same outcomes
    again = [
        (evaluate_line(f, c, n, t), evaluate_reachability(f, c, n, t))
        for f, c, n in _random_inputs(20261006, 1000)
    ]
    assert again == first
    kinds = {o.kind for pair in first for o in pair}
    assert kinds == {"ok", "changed", "refuse"}, "the random inputs should exercise every outcome kind"


@settings(max_examples=200, deadline=None, derandomize=True)
@given(facts=facts_st, consent=consent_st, now=now_st)
def test_evaluate_twice_is_identical_property(facts: Facts, consent: ConsentView, now: datetime) -> None:
    t = default_thresholds()
    a1, a2 = evaluate_line(facts, consent, now, t), evaluate_line(facts, consent, now, t)
    b1, b2 = evaluate_reachability(facts, consent, now, t), evaluate_reachability(facts, consent, now, t)
    assert a1 == a2 and b1 == b2
    # with the default thresholds object too
    assert evaluate_line(facts, consent, now) == a1
    assert evaluate_reachability(facts, consent, now) == b1
    # phrasing is deterministic as well
    assert phrase(a1, facts, alias="Mom", tz="America/Toronto", form="sms", now=now) == phrase(
        a1, facts, alias="Mom", tz="America/Toronto", form="sms", now=now
    )


@settings(max_examples=100, deadline=None, derandomize=True)
@given(facts=facts_st, consent=consent_st, now=now_st)
def test_inputs_are_not_mutated(facts: Facts, consent: ConsentView, now: datetime) -> None:
    before = (facts.model_dump(), consent.model_dump())
    evaluate_line(facts, consent, now)
    evaluate_reachability(facts, consent, now)
    assert (facts.model_dump(), consent.model_dump()) == before
    with pytest.raises(Exception, match="frozen"):
        facts.sim_swapped = True


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Facts(fetched_at=datetime(2026, 10, 6, 12, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        ConsentView(bound=True, grant="owner", revoked_at=datetime(2026, 10, 6, 12, 0))


FORBIDDEN = {
    "clock": re.compile(r"\b(datetime|date)\.(now|today|utcnow)\s*\(|\bdatetime\.now\b"),
    "time module": re.compile(r"^\s*(import time\b|from time import)", re.M),
    "random": re.compile(
        r"^\s*(import random\b|from random import|import secrets\b|from secrets import)", re.M
    ),
    "os.environ": re.compile(r"os\.environ|os\.getenv|environ\["),
    "network": re.compile(
        r"^\s*(import|from)\s+(socket|http|urllib|httpx|requests|aiohttp|boto3|botocore)\b", re.M
    ),
    "logging / print": re.compile(r"^\s*(import logging\b|from logging import)|\bprint\s*\(", re.M),
    "subprocess": re.compile(r"^\s*(import subprocess\b|from subprocess import)", re.M),
}


@pytest.mark.parametrize("name", sorted(FORBIDDEN))
def test_source_is_pure(name: str) -> None:
    pattern = FORBIDDEN[name]
    offenders = []
    for py in sorted(SRC.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{py.relative_to(SRC)}:{line}: {m.group(0).strip()}")
    assert not offenders, f"{name} found in src/: {offenders}"


def test_only_thresholds_loader_touches_the_filesystem() -> None:
    io_pattern = re.compile(r"\.(read_text|read_bytes|open)\s*\(|\bopen\s*\(")
    allowed = {"thresholds.py", "version.py"}
    for py in sorted(SRC.rglob("*.py")):
        hits = io_pattern.findall(py.read_text(encoding="utf-8"))
        assert not hits or py.name in allowed, f"{py.name} does I/O: {hits}"
