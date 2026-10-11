"""Shared builders for the policy tests. Every datetime here is aware UTC; `NOW` is fixed."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from tower_policy import ConsentView, Facts, Thresholds, default_thresholds

NOW = datetime(2026, 10, 6, 14, 30, tzinfo=UTC)

FactsBuilder = Callable[..., Facts]
ConsentBuilder = Callable[..., ConsentView]


@pytest.fixture(scope="session")
def now() -> datetime:
    return NOW


@pytest.fixture(scope="session")
def thresholds() -> Thresholds:
    return default_thresholds()


@pytest.fixture(scope="session")
def make_facts(thresholds: Thresholds) -> FactsBuilder:
    """Facts with a fresh `fetched_at` by default; pass `stale=True` for one past STALE."""

    def _make(stale: bool = False, **kw: object) -> Facts:
        fetched = NOW - (thresholds.STALE + timedelta(seconds=1) if stale else timedelta(seconds=5))
        return Facts(fetched_at=fetched, **kw)

    return _make


@pytest.fixture(scope="session")
def make_consent() -> ConsentBuilder:
    def _make(bound: bool = True, grant: str = "owner", revoked: bool = False) -> ConsentView:
        return ConsentView(
            bound=bound,
            grant=grant,
            revoked_at=NOW - timedelta(hours=1) if revoked else None,
        )

    return _make
