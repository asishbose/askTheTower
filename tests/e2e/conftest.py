"""End-to-end layer (testing-and-showcase §3): runs against a stack that is already up — `make up` for ENV=local,
`make deploy-eks` / `make deploy` for eks / aws. With no stack the tests skip with the reason (they never start
one themselves). Endpoints, `mock_admin` and the running-stack check come from the root conftest.

Chaos runs last: it injects carrier faults and may leave a circuit breaker open for up to 60 s, which must not
leak into the demo / showcase tests' transcripts.
"""

from __future__ import annotations

import pytest

from tests.helpers.env import Stack


@pytest.fixture(scope="session")
def stack(running_stack: Stack) -> Stack:
    """Session-scoped here (module fixtures such as the log capture depend on it)."""
    return running_stack


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    chaos = [i for i in items if "tests/e2e/test_chaos.py" in i.nodeid.replace("\\", "/")]
    if chaos:
        items[:] = [i for i in items if i not in chaos] + chaos
