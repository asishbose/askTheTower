"""Static and offline checks on the EKS target (prompt 14): deploy/terraform/eks + deploy/terraform/modules/eks.

- Layout: the module and the second root exist; the root's backend is S3 with placeholders and its own key.
- Node group: 2 x t4g.medium, ON_DEMAND, arm64 AMI.
- IRSA: no "*" resource in irsa.tf except the one SmsToPhoneNumber statement (SMS to a phone has no ARN).
- Outputs: the names scripts/render_values.py consumes.
- `terraform fmt -check`, `init -backend=false` + `validate`, and `terraform test` (mock provider; no AWS calls).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.aws.conftest import TF
from tests.aws.test_terraform_static import terraform_bin

EKS_ROOT = TF / "eks"
EKS_MODULE = TF / "modules" / "eks"
RENDER_VALUES_OUTPUTS = {
    "region",
    "cluster_name",
    "cluster_endpoint",
    "namespace",
    "vpc_id",
    "vpc_cidr",
    "public_subnet_ids",
    "irsa_role_arns",
    "ecr_repositories",
    "table_prefix",
    "kms_key_arn",
    "kms_hmac_key_arn",
    "sns_topic_arns",
    "bedrock_model_id",
    "tower_host",
    "binding_host",
    "hooks_host",
    "certificate_arn",
    "tower_url",
    "binding_url",
    "hooks_base_url",
    "mock_url",
    "carrier_backend",
}


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _block(text: str, header: str) -> str:
    """The `{ ... }` body that follows `header` (brace-matched)."""
    start = text.index(header)
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i : j + 1]
    raise AssertionError(f"unbalanced block after {header!r}")


@pytest.mark.unit
def test_module_layout() -> None:
    for f in ("main.tf", "variables.tf", "outputs.tf", "irsa.tf", "lb-controller-iam-policy.json"):
        assert (EKS_MODULE / f).exists(), f
    assert (EKS_MODULE / "tests" / "irsa.tftest.hcl").exists()


@pytest.mark.unit
def test_root_files_and_backend_placeholders() -> None:
    for f in (
        "main.tf",
        "providers.tf",
        "variables.tf",
        "outputs.tf",
        "backend.tf",
        "README.md",
        "envs/eks.tfvars.example",
        ".terraform.lock.hcl",
    ):
        assert (EKS_ROOT / f).exists(), f
    backend = (EKS_ROOT / "backend.tf").read_text()
    assert 'backend "s3"' in backend and "REPLACE-ME" in backend
    assert 'key            = "ask-the-tower/eks/terraform.tfstate"' in backend
    main = (EKS_ROOT / "main.tf").read_text()
    assert 'data "terraform_remote_state" "aws"' in main and 'source = "../modules/eks"' in main


@pytest.mark.unit
def test_outputs_match_render_values_contract() -> None:
    names = set(re.findall(r'^output "([\w-]+)"', (EKS_ROOT / "outputs.tf").read_text(), re.M))
    assert RENDER_VALUES_OUTPUTS <= names, RENDER_VALUES_OUTPUTS - names
    # 13's root exports what the EKS root reads from its state.
    aws_outputs = set(re.findall(r'^output "([\w-]+)"', (TF / "outputs.tf").read_text(), re.M))
    needed = {"name", "region", "vpc_id", "vpc_cidr", "public_subnet_ids", "private_subnet_ids", "table_arns"}
    assert needed | {"ecr_repositories", "sns_topic_arns", "kms_key_arn", "kms_hmac_key_arn"} <= aws_outputs


@pytest.mark.unit
def test_node_group_is_two_on_demand_graviton_nodes() -> None:
    variables = (EKS_MODULE / "variables.tf").read_text()
    main = (EKS_MODULE / "main.tf").read_text()

    def default(var: str) -> str:
        m = re.search(r"default\s*=\s*(.+)", _block(variables, f'variable "{var}"'))
        assert m, var
        return m.group(1).strip()

    assert default("node_instance_types") == '["t4g.medium"]'
    assert default("capacity_type") == '"ON_DEMAND"'
    assert default("node_ami_type") == '"AL2023_ARM_64_STANDARD"'
    assert default("node_desired") == "2"
    ng = _block(main, 'resource "aws_eks_node_group" "default"')
    for wiring in (
        "instance_types = var.node_instance_types",
        "capacity_type  = var.capacity_type",
        "ami_type       = var.node_ami_type",
        "desired_size = var.node_desired",
    ):
        assert wiring in ng, wiring
    # The root does not override them with anything else.
    root_vars = (EKS_ROOT / "variables.tf").read_text()
    assert '"t4g.medium"' in root_vars and '"ON_DEMAND"' in root_vars


@pytest.mark.unit
def test_irsa_has_one_wildcard_and_it_is_sms() -> None:
    irsa = _strip_comments((EKS_MODULE / "irsa.tf").read_text())
    assert irsa.count('"*"') == 1, "exactly one wildcard resource"
    idx = irsa.index('"*"')
    stmt_start = irsa.rindex("{", 0, idx)
    stmt = irsa[stmt_start : irsa.index("}", idx) + 1]
    assert 'Sid      = "SmsToPhoneNumber"' in stmt and 'Action   = ["sns:Publish"]' in stmt, stmt
    # Policies are jsonencode()d objects (assertable under a mock provider), not aws_iam_policy_document.
    assert "aws_iam_policy_document" not in irsa
    # Trust is pinned to one service account and the STS audience.
    assert "system:serviceaccount:${sa.namespace}:${sa.name}" in irsa and ":aud" in irsa
    # ref-client: Bedrock only.
    ref = _block(irsa, '"ref-client" = {\n      Version')
    assert set(re.findall(r'"(\w+:\w+)"', ref)) == {
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
    }


@pytest.mark.unit
def test_no_secrets_or_digit_runs_in_eks_files() -> None:
    from tests.privacy.patterns import phone_hits

    files = [*EKS_ROOT.glob("*.tf"), *EKS_MODULE.glob("*.tf"), EKS_MODULE / "tests" / "irsa.tftest.hcl"]
    files += [EKS_ROOT / "README.md", EKS_ROOT / "envs" / "eks.tfvars.example"]
    for f in files:
        assert not phone_hits(f.read_text()), f


@pytest.mark.unit
def test_terraform_fmt_eks() -> None:
    tf = terraform_bin()
    if tf is None:
        pytest.skip("terraform CLI not installed (PATH or ~/tools/terraform)")
    for d in (EKS_ROOT, EKS_MODULE):
        r = subprocess.run([tf, "fmt", "-check", "-recursive"], cwd=d, capture_output=True, text=True)  # noqa: S603
        assert r.returncode == 0, f"{d}: {r.stdout}{r.stderr}"


def _init(tf: str, cwd: Path) -> None:
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    init = subprocess.run(  # noqa: S603
        [tf, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    if init.returncode != 0 and ("registry.terraform.io" in init.stderr or "dial tcp" in init.stderr):
        pytest.skip("terraform init could not reach the provider registry (network)")
    assert init.returncode == 0, init.stderr


@pytest.mark.integration
def test_terraform_validate_eks_root() -> None:
    """`init -backend=false` + `validate` on the second root: downloads providers, never touches AWS."""
    tf = terraform_bin()
    if tf is None:
        pytest.skip("terraform CLI not installed (PATH or ~/tools/terraform)")
    _init(tf, EKS_ROOT)
    r = subprocess.run([tf, "validate", "-no-color"], cwd=EKS_ROOT, capture_output=True, text=True)  # noqa: S603
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.integration
def test_terraform_test_irsa_policies() -> None:
    """`terraform test` in modules/eks: mock AWS provider, asserts the rendered IRSA JSON (no credentials)."""
    tf = terraform_bin()
    if tf is None:
        pytest.skip("terraform CLI not installed (PATH or ~/tools/terraform)")
    _init(tf, EKS_MODULE)
    r = subprocess.run(  # noqa: S603
        [tf, "test", "-no-color"], cwd=EKS_MODULE, capture_output=True, text=True, timeout=600
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 failed" in r.stdout
