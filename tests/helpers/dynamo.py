"""DynamoDB for tests (RUN-ALL Decisions): moto in-process always; DynamoDB Local via testcontainers when
`docker info` succeeds. The root conftest turns these into session fixtures, so a full run starts moto once and
one DynamoDB Local container — not one per package. `TOWER_DDB_BACKENDS=moto` (or `local`) narrows the matrix."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from typing import Any

FAKE_AWS = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1",
}
DYNAMODB_LOCAL_IMAGE = "amazon/dynamodb-local:latest"
NO_DOCKER = "Docker is not available (docker info failed): DynamoDB Local cases skipped; moto cases ran"


@cache
def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0  # noqa: S603,S607
    except (OSError, subprocess.TimeoutExpired):
        return False


def backends() -> list[str]:
    """The DynamoDB backends a parametrised `store` fixture runs on."""
    return [b for b in os.environ.get("TOWER_DDB_BACKENDS", "moto,local").split(",") if b]


@contextmanager
def fake_aws_env() -> Iterator[None]:
    old = {k: os.environ.get(k) for k in FAKE_AWS}
    os.environ.update(FAKE_AWS)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def dynamodb_local() -> Iterator[str]:
    """Start DynamoDB Local (in-memory, shared db) and yield its endpoint URL."""
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = (
        DockerContainer(DYNAMODB_LOCAL_IMAGE)
        .with_exposed_ports(8000)
        .with_command("-jar DynamoDBLocal.jar -inMemory -sharedDb")
    )
    with container:
        wait_for_logs(container, "Initializing DynamoDB Local", timeout=60)
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8000)}"


def client_for(request: Any, backend: str) -> Any:
    """A boto3 DynamoDB client on `backend` ("moto" | "local") for a parametrised `store` fixture; pulls the
    root conftest's session fixtures (`moto_dynamodb` / `dynamodb_local_endpoint`) through `request`."""
    import boto3

    if backend == "moto":
        return request.getfixturevalue("moto_dynamodb")
    if backend == "local":
        endpoint = request.getfixturevalue("dynamodb_local_endpoint")
        return boto3.client("dynamodb", region_name="us-east-1", endpoint_url=endpoint)
    raise ValueError(f"unknown DynamoDB backend {backend!r}")
