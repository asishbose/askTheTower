"""Root conftest: what every test layer shares (prompt 18).

- **Markers.** Every collected test carries exactly one of `unit` / `integration` / `e2e` / `nightly`
  (`tests/helpers/markers.py`); anything else is a collection error, on top of `--strict-markers`.
- **DynamoDB, once per run.** `moto_aws` / `moto_dynamodb` (one `mock_aws()` for the whole session) and
  `dynamodb_local_endpoint` (one DynamoDB Local container via testcontainers, skipped with the reason when
  `docker info` fails). Package conftests build their per-test `store` on these instead of starting their own.
- **ENV.** `env` → `Targets` (base URLs for ENV=local from compose, eks/aws from the rendered outputs);
  `mock_admin` (load scenario, fire event, advance clock, inject fault); `stack` — for an e2e test the running
  environment (no-op: skips when it is not answering, never starts one), for an integration test the
  containers (DynamoDB Local when Docker is up, moto otherwise).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.helpers import dynamo
from tests.helpers.env import MockAdmin, Stack, Targets, answers, resolve
from tests.helpers.markers import layer_violations


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    bad = layer_violations(items)
    if bad:
        raise pytest.UsageError("tests without exactly one layer marker:\n  " + "\n  ".join(bad))


def pytest_report_header(config: pytest.Config) -> str:
    return (
        f"DynamoDB backends: {dynamo.backends()}; docker available: {dynamo.docker_available()}; "
        f"ENV={resolve().env}"
    )


# --- DynamoDB (shared by every package's `store` fixture) -----------------------------------------------------


@pytest.fixture(scope="session")
def fake_aws_env() -> Iterator[None]:
    """moto / DynamoDB Local credentials. Not autouse: the live (nightly) tests must see the real environment."""
    with dynamo.fake_aws_env():
        yield


@pytest.fixture(scope="session")
def docker_available() -> bool:
    return dynamo.docker_available()


@pytest.fixture(scope="session")
def moto_aws(fake_aws_env: None) -> Iterator[None]:
    """One `mock_aws()` for the session (starting moto costs tens of seconds on /mnt/c). Tests that start their
    own nested `mock_aws()` share its backend state, so they clean up what they create."""
    from moto import mock_aws

    with mock_aws():
        yield


@pytest.fixture(scope="session")
def moto_dynamodb(moto_aws: None) -> Any:
    import boto3

    return boto3.client("dynamodb", region_name="us-east-1")


@pytest.fixture(scope="session")
def dynamodb_local_endpoint(fake_aws_env: None) -> Iterator[str]:
    if not dynamo.docker_available():
        pytest.skip(dynamo.NO_DOCKER)
    with dynamo.dynamodb_local() as url:
        yield url


# --- ENV, the mock's admin API, the stack ---------------------------------------------------------------------


@pytest.fixture(scope="session")
def env() -> Targets:
    """ENV=local|eks|aws → base URLs (compose defaults; deploy/.env.<env> rendered from helm/terraform outputs)."""
    return resolve()


@pytest.fixture(scope="session")
def running_stack(env: Targets) -> Stack:
    missing = [u for u in env.health_urls() if not answers(u)]
    if missing:
        hint = "make up" if env.env == "local" else f"make deploy{'-eks' if env.env == 'eks' else ''}"
        pytest.skip(
            f"ENV={env.env} stack is not running ({', '.join(missing)} not answering): run `{hint}` first"
        )
    return Stack("e2e", env.env, env.tower_url, env.mock_url, env.binding_url, env.alerts_url)


@pytest.fixture(scope="session")
def container_stack(request: pytest.FixtureRequest) -> Stack:
    if dynamo.docker_available() and "local" in dynamo.backends():
        return Stack(
            "integration", "local", dynamodb_endpoint=request.getfixturevalue("dynamodb_local_endpoint")
        )
    request.getfixturevalue("moto_aws")
    return Stack("integration", "local", dynamodb_endpoint=None)


@pytest.fixture
def stack(request: pytest.FixtureRequest) -> Stack:
    """e2e → the running environment (skip if down); anything else → testcontainers (or moto)."""
    if request.node.get_closest_marker("e2e") or request.node.get_closest_marker("nightly"):
        return request.getfixturevalue("running_stack")  # type: ignore[no-any-return]
    return request.getfixturevalue("container_stack")  # type: ignore[no-any-return]


@pytest.fixture(scope="session")
def mock_admin(env: Targets, running_stack: Stack) -> Iterator[MockAdmin]:
    """The mock carrier's admin API on the running ENV. On aws the mock is internal (no public URL)."""
    if not env.mock_url:
        pytest.skip(
            f"ENV={env.env}: the mock carrier has no public URL (internal ALB); its admin API is reached through "
            "`scripts/aws_seed.py` (ECS Exec) — run the nightly chaos job there"
        )
    admin = MockAdmin(env.mock_url, token=env.mock_admin_token)
    yield admin
    try:
        admin.clear_faults()
    finally:
        admin.close()
