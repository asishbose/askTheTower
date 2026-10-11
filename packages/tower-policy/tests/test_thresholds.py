"""Thresholds file: valid file loads; a file past the API maximum is rejected; a missing file is rejected."""

from datetime import timedelta
from pathlib import Path

import pytest
from tower_policy import (
    DEFAULT_THRESHOLDS_PATH,
    ThresholdError,
    Thresholds,
    default_thresholds,
    load_thresholds,
    parse_thresholds,
)
from tower_policy.thresholds import parse_duration

pytestmark = pytest.mark.unit

VALID = """
SWAP_WINDOW: 72h
STALE: 10m
FRESH: 10m
UNREACHABLE_ALERT: {transplant: 20m, care: 4h}
ESCALATE_NEXT: 15m
RATE_LIMIT: 6h
ACK_DISTRUST: 24h
"""


def test_packaged_file_loads_with_the_documented_values() -> None:
    t = load_thresholds()
    assert t == load_thresholds(DEFAULT_THRESHOLDS_PATH) == default_thresholds()
    assert t.SWAP_WINDOW == timedelta(hours=72)
    assert t.STALE == timedelta(minutes=10)
    assert t.FRESH == timedelta(minutes=10)
    assert t.UNREACHABLE_ALERT.transplant == timedelta(minutes=20)
    assert t.UNREACHABLE_ALERT.care == timedelta(hours=4)
    assert t.ESCALATE_NEXT == timedelta(minutes=15)
    assert t.RATE_LIMIT == timedelta(hours=6)
    assert t.ACK_DISTRUST == timedelta(hours=24)


def test_valid_file_loads(tmp_path: Path) -> None:
    p = tmp_path / "t.yaml"
    p.write_text(VALID)
    t = load_thresholds(p)
    assert isinstance(t, Thresholds)
    assert t == parse_thresholds(VALID) == load_thresholds(str(p))


def test_swap_window_at_the_api_maximum_is_accepted() -> None:
    assert parse_thresholds(VALID.replace("72h", "2400h")).SWAP_WINDOW == timedelta(hours=2400)
    assert parse_thresholds(VALID.replace("72h", "100d")).SWAP_WINDOW == timedelta(days=100)


def test_swap_window_past_the_api_maximum_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "wide.yaml"
    p.write_text(VALID.replace("72h", "2401h"))
    with pytest.raises(ThresholdError, match="SWAP_WINDOW"):
        load_thresholds(p)
    with pytest.raises(ThresholdError, match="SWAP_WINDOW"):
        parse_thresholds(VALID.replace("72h", "101d"))


def test_stale_above_one_hour_is_rejected() -> None:
    assert parse_thresholds(VALID.replace("STALE: 10m", "STALE: 60m")).STALE == timedelta(hours=1)
    with pytest.raises(ThresholdError, match="STALE"):
        parse_thresholds(VALID.replace("STALE: 10m", "STALE: 61m"))


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ThresholdError, match="not found"):
        load_thresholds(tmp_path / "nope.yaml")


def test_missing_key_is_rejected() -> None:
    with pytest.raises(ThresholdError, match="ACK_DISTRUST"):
        parse_thresholds(VALID.replace("ACK_DISTRUST: 24h\n", ""))


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ThresholdError):
        parse_thresholds(VALID + "GRACE: 5m\n")


def test_malformed_values_are_rejected() -> None:
    for bad in ("STALE: ten minutes", "STALE: -5m", "STALE: 0s", "STALE: true"):
        with pytest.raises(ThresholdError):
            parse_thresholds(VALID.replace("STALE: 10m", bad))
    with pytest.raises(ThresholdError):
        parse_thresholds("- just\n- a list\n")
    with pytest.raises(ThresholdError):
        parse_thresholds("SWAP_WINDOW: [unclosed\n")


def test_duration_grammar() -> None:
    assert parse_duration("30s") == timedelta(seconds=30)
    assert parse_duration("2d") == timedelta(days=2)
    assert parse_duration("1.5h") == timedelta(minutes=90)
    assert parse_duration(90) == timedelta(seconds=90)
    assert parse_duration(timedelta(minutes=1)) == timedelta(minutes=1)
    with pytest.raises(ThresholdError):
        parse_duration("5 fortnights")


def test_thresholds_are_frozen() -> None:
    t = default_thresholds()
    with pytest.raises(Exception, match="frozen"):
        t.STALE = timedelta(hours=5)
