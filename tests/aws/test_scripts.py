"""The AWS scripts against a fake `terraform output -json` (offline: nothing here calls AWS)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.aws.conftest import ROOT

pytestmark = pytest.mark.unit

OUTPUTS = {
    "region": "us-east-1",
    "tower_mcp_url": "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Ax/invocations?qualifier=DEFAULT",
    "binding_url": "https://dexample.cloudfront.net",
    "hooks_base_url": "https://dexample.cloudfront.net",
    "table_prefix": "att-dev-",
    "kms_key_arn": "arn:aws:kms:us-east-1:acct:key/main",
    "kms_hmac_key_arn": "arn:aws:kms:us-east-1:acct:key/hmac",
    "carrier_backend": "mock",
    "carrier_base_url": "https://mock.carrier.example.org",
    "gateway_url": "https://gw.example/mcp",
    "mock_cluster_name": "att-dev-mock",
    "mock_service_name": "mock-carrier",
    "mock_container_name": "mock-carrier",
    "ecr_repositories": {
        s: f"acct.dkr.ecr.us-east-1.amazonaws.com/att-dev/{s}"
        for s in ("mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client")
    },
}


@pytest.fixture
def outputs(tmp_path: Path) -> Path:
    p = tmp_path / "tf-outputs.json"
    p.write_text(
        json.dumps({k: {"value": v, "type": "string", "sensitive": False} for k, v in OUTPUTS.items()})
    )
    return p


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=120)  # noqa: S603


def test_render_env_writes_a_make_include(outputs: Path, tmp_path: Path) -> None:
    out = tmp_path / "env.aws"
    r = run("scripts/render_env.py", "--env", "aws", "--outputs", str(outputs), "--out", str(out))
    assert r.returncode == 0, r.stderr
    env = dict(
        line.split("=", 1) for line in out.read_text().splitlines() if line and not line.startswith("#")
    )
    assert env["TOWER_URL"] == OUTPUTS["tower_mcp_url"]
    assert env["ALERTS_URL"] == OUTPUTS["hooks_base_url"] and env["CARRIER_GATEWAY_AUTH"] == "sigv4"
    assert env["DYNAMO_ENDPOINT"] == "" and "SECRET" not in out.read_text()


def test_aws_seed_dry_run_names_the_task_and_the_tables(outputs: Path) -> None:
    r = run("scripts/aws_seed.py", "--outputs", str(outputs), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "aws ecs execute-command" in r.stdout and "/_admin/scenarios/load" in r.stdout
    assert "att-dev-*" in r.stdout
    from tests.privacy.patterns import phone_hits

    assert not phone_hits(r.stdout)  # numbers never leave the scenario file in output


def test_push_images_dry_run_builds_arm64(outputs: Path) -> None:
    r = run(
        "scripts/push_images.py", "--env", "aws", "--tag", "abc1234", "--dry-run", "--outputs", str(outputs)
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("--platform linux/arm64") == 5
    # single-manifest images: Lambda and AgentCore Runtime reject the OCI index that attestations produce
    assert r.stdout.count("--provenance=false --sbom=false") == 5
    assert "att-dev/tower-mcp:abc1234" in r.stdout and "docker login" in r.stdout


def _ecr_root_outputs(tmp_path: Path) -> Path:
    """What `make ecr-outputs` writes: the ECR root alone, before the main root was ever applied."""
    repos = OUTPUTS["ecr_repositories"]
    ecr = {
        "ecr_repositories": repos,
        "registry": "acct.dkr.ecr.us-east-1.amazonaws.com",
        "region": "us-east-1",
    }
    p = tmp_path / "tf-outputs-ecr.json"
    p.write_text(json.dumps({k: {"value": v, "sensitive": False} for k, v in ecr.items()}))
    return p


def test_push_images_works_from_the_ecr_root_outputs_alone(tmp_path: Path) -> None:
    r = run("scripts/push_images.py", "--env", "aws", "--tag", "abc1234", "--dry-run",
            "--outputs", str(_ecr_root_outputs(tmp_path)))  # fmt: skip
    assert r.returncode == 0, r.stderr
    for s in OUTPUTS["ecr_repositories"]:
        assert f"att-dev/{s}:abc1234" in r.stdout


def test_push_images_verify_checks_the_tag_in_every_repository(tmp_path: Path) -> None:
    r = run("scripts/push_images.py", "--env", "aws", "--tag", "abc1234", "--verify", "--dry-run",
            "--outputs", str(_ecr_root_outputs(tmp_path)))  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("aws ecr describe-images") == 5 and "imageTag=abc1234" in r.stdout
    assert "--repository-name att-dev/tower-mcp" in r.stdout and "buildx" not in r.stdout


def test_missing_outputs_say_what_to_run(tmp_path: Path) -> None:
    r = run("scripts/render_env.py", "--env", "aws", "--outputs", str(tmp_path / "none.json"))
    assert r.returncode != 0 and "make deploy ENV=aws" in r.stderr
