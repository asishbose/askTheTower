"""Static checks on deploy/terraform (offline; prompt 13 guardrails).

- The DynamoDB definitions Terraform applies are generated from the Python tables and are current (CI check).
- Every module named in prompt 13's Deliverables exists with main/variables/outputs.
- No metric — dashboard widget or metric filter — carries a per-line label (10 §2).
- No inline secret in any .tf file (the tf-lint rule for inline secrets; gitleaks covers the rest).
- One Gateway target per vendored spec; the specs decode and carry `{apiRoot}` servers to rewrite.
- `terraform fmt -check` and `terraform validate` (init -backend=false; never plan/apply) when terraform is present.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.aws.conftest import ROOT, TF

MODULES = [
    "dynamodb",
    "kms",
    "network",
    "mock_carrier",
    "agentcore_runtime",
    "agentcore_gateway",
    "agentcore_identity",
    "lambdas",
    "scheduler",
    "sns",
    "observability",
    "cognito",
    "web_chat",
]
TF_FILES = sorted(p for p in TF.rglob("*.tf") if ".terraform" not in p.parts)


def terraform_bin() -> str | None:
    found = shutil.which("terraform")
    if found:
        return found
    home = Path.home() / "tools" / "terraform"
    return str(home) if home.exists() else None


@pytest.mark.unit
def test_tables_tfvars_is_current() -> None:
    gen = TF / "modules" / "dynamodb" / "generate.py"
    r = subprocess.run([sys.executable, str(gen), "--check"], capture_output=True, text=True, cwd=ROOT)  # noqa: S603
    assert r.returncode == 0, r.stderr
    doc = json.loads((TF / "tables.auto.tfvars.json").read_text())["dynamodb_tables"]
    assert sorted(doc) == ["AlertsState", "Audit", "BindTokens", "Grants", "Lines", "Users", "Watches"]
    assert doc["Audit"]["point_in_time_recovery"] is True
    assert doc["BindTokens"]["ttl"] == {"enabled": True, "attribute_name": "expires_at"}
    assert doc["AlertsState"]["ttl"]["enabled"] is True


@pytest.mark.unit
@pytest.mark.parametrize("module", MODULES)
def test_module_layout(module: str) -> None:
    d = TF / "modules" / module
    for f in ("main.tf", "variables.tf", "outputs.tf"):
        assert (d / f).exists(), f"{d}/{f}"


@pytest.mark.unit
def test_root_files_and_backend_placeholders() -> None:
    for f in (
        "main.tf",
        "providers.tf",
        "variables.tf",
        "outputs.tf",
        "backend.tf",
        "envs/aws.tfvars.example",
    ):
        assert (TF / f).exists(), f
    backend = (TF / "backend.tf").read_text()
    assert 'backend "s3"' in backend and "REPLACE-ME" in backend


@pytest.mark.unit
def test_ecr_is_its_own_root_and_the_main_root_only_reads_it() -> None:
    """`make down` (main root) must never delete images: the repositories live in deploy/terraform/ecr, own state."""
    ecr = TF / "ecr"
    for name in ("main.tf", "providers.tf", "variables.tf", "outputs.tf", "backend.tf"):
        assert (ecr / name).exists(), name
    keys = {
        root: re.search(r'key\s*=\s*"([^"]+)"', (root / "backend.tf").read_text()).group(1)  # type: ignore[union-attr]
        for root in (TF, ecr, TF / "eks")
    }
    assert len(set(keys.values())) == 3, keys
    repos = (ecr / "main.tf").read_text()
    assert (
        repos.count('resource "aws_ecr_repository" "service"') == 1 and "force_delete         = true" in repos
    )
    assert "scan_on_push = true" in repos and 'resource "aws_ecr_lifecycle_policy" "service"' in repos
    outputs = (ecr / "outputs.tf").read_text()
    assert all(f'output "{o}"' in outputs for o in ("ecr_repositories", "registry", "region"))
    for f in TF_FILES:
        if ecr not in f.parents:
            assert 'resource "aws_ecr_' not in f.read_text(), (
                f"{f} manages ECR; only deploy/terraform/ecr may"
            )
    assert 'data "aws_ecr_repository" "service"' in (TF / "main.tf").read_text()
    assert 'output "ecr_repositories"' in (TF / "outputs.tf").read_text()  # push_images.py / eks keep working


def _metric_arrays(widget_props: dict) -> list[list]:
    return [m for m in widget_props.get("metrics", []) if isinstance(m, list)]


@pytest.mark.unit
def test_dashboard_has_no_per_line_labels() -> None:
    tpl = (TF / "modules" / "observability" / "dashboard.json.tftpl").read_text()
    rendered = re.sub(
        r"\$\{(\w+)\}", lambda m: "400" if m.group(1) == "latency_budget_ms" else f"x-{m.group(1)}", tpl
    )
    dash = json.loads(rendered)
    assert dash["widgets"], "dashboard has widgets"
    for w in dash["widgets"]:
        props = w["properties"]
        for metric in _metric_arrays(props):
            # [namespace, metric name, dim1 name, dim1 value, dim2 name, ...(, {options})]
            parts = [x for x in metric if isinstance(x, str)]
            dims = parts[2:]
            names, values = dims[0::2], dims[1::2]
            assert not any("line" in n.lower() for n in names), metric
            assert not any(v.startswith("ln_") for v in values), metric
        query = props.get("query", "")
        for by in re.findall(r"\bby\s+([\w.,\s]+)", query):
            assert "line" not in by.lower(), query
    assert "p95" in tpl and "AuditReconcileMisses" in tpl and "AlertsSent" in tpl


@pytest.mark.unit
def test_metric_filters_have_no_dimensions_and_guard_exists() -> None:
    obs = (TF / "modules" / "observability" / "main.tf").read_text()
    blocks = re.findall(r"metric_transformation\s*\{(.*?)\n\s*\}", obs, re.S)
    assert blocks
    for b in blocks:
        assert "dimensions" not in b, b
    assert "LineIdOnMetric" in obs and 'CloudWatchMetrics\\" \\"line_id' in obs  # guard filter + alarm
    for f in TF_FILES:
        text = f.read_text()
        for m in re.finditer(r"dimensions\s*=\s*\{([^}]*)\}", text):
            assert "line" not in m.group(1).lower(), f"{f}: {m.group(0)}"


SECRET_ATTR = re.compile(
    r'^\s*(\w*(?:secret|password|token|api_key|private_key)\w*)\s*=\s*"([^"$]*)"', re.I | re.M
)
ALLOWED_LITERALS = {"", "REPLACE-ME"}


@pytest.mark.unit
def test_no_inline_secrets_in_terraform() -> None:
    offenders = []
    for f in TF_FILES + [TF / "envs" / "aws.tfvars.example"]:
        for m in SECRET_ATTR.finditer(f.read_text()):
            name, value = m.group(1), m.group(2)
            if value in ALLOWED_LITERALS or name.endswith(("_ref", "_arn", "_name", "_location", "_source")):
                continue
            if value.startswith("env:"):
                continue
            offenders.append(f"{f.relative_to(ROOT)}: {name} = {value!r}")
    assert offenders == []


@pytest.mark.unit
def test_no_carrier_secret_outside_identity_on_the_gateway_path() -> None:
    """Guardrail: with Identity in use, the carrier client secret reaches only AgentCore Identity (and the mock's
    own registry, which issues it). Tower's environment carries it only in the cut-line (direct) branch."""
    main = (TF / "main.tf").read_text()
    gateway_branch = main.split('var.tower_carrier_client == "gateway" ? {', 1)[1].split("} : {", 1)[0]
    assert "SECRET" not in gateway_branch
    mock = (TF / "modules" / "mock_carrier" / "main.tf").read_text()
    assert "aws_secretsmanager_secret" in mock  # the mock's own registry (10 §2)
    for module in ("agentcore_runtime", "lambdas", "agentcore_gateway"):
        text = (TF / "modules" / module / "main.tf").read_text()
        assert 'aws_secretsmanager_secret"' not in text, module


@pytest.mark.unit
def test_one_gateway_target_per_spec() -> None:
    specs = sorted((ROOT / "specs" / "camara").glob("*.yaml"))
    assert len(specs) == 6
    for spec in specs:
        doc = yaml.safe_load(spec.read_text())
        assert doc["servers"][0]["url"].startswith("{apiRoot}/"), spec.name
    gw = (TF / "modules" / "agentcore_gateway" / "main.tf").read_text()
    assert 'fileset(var.specs_dir, "*.yaml")' in gw and "{apiRoot}" in gw
    assert 'authorizer_type = "AWS_IAM"' in gw and "AUTHORIZATION_CODE" in gw and "CLIENT_CREDENTIALS" in gw


@pytest.mark.unit
def test_schedules_match_10_section_3() -> None:
    sched = (TF / "modules" / "scheduler" / "main.tf").read_text()
    for expr, payload in [
        ("rate(5 minutes)", 'profile = "transplant"'),
        ("rate(30 minutes)", 'profile = "care"'),
        ("cron(0 8 * * ? *)", 'profile = "self"'),
        ("cron(0 3 * * ? *)", "scheduled_time"),
    ]:
        assert expr in sched and payload in sched


@pytest.mark.unit
def test_no_phone_numbers_or_account_ids_in_deploy() -> None:
    from tests.privacy.patterns import phone_hits

    for f in TF_FILES + [TF / "envs" / "aws.tfvars.example", TF / "README.md"]:
        assert not phone_hits(f.read_text()), f


@pytest.mark.unit
def test_terraform_fmt() -> None:
    tf = terraform_bin()
    if tf is None:
        pytest.skip("terraform CLI not installed (PATH or ~/tools/terraform)")
    r = subprocess.run([tf, "fmt", "-check", "-recursive"], cwd=TF, capture_output=True, text=True)  # noqa: S603
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.integration
@pytest.mark.parametrize("root", [".", "ecr"])
def test_terraform_validate(root: str) -> None:
    """`init -backend=false` + `validate`: downloads the AWS provider (network) but never touches AWS."""
    tf = terraform_bin()
    if tf is None:
        pytest.skip("terraform CLI not installed (PATH or ~/tools/terraform)")
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    init = subprocess.run(  # noqa: S603
        [tf, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=TF / root,
        capture_output=True,
        text=True,
        env=env,
    )
    if init.returncode != 0 and ("registry.terraform.io" in init.stderr or "dial tcp" in init.stderr):
        pytest.skip("terraform init could not reach the provider registry (network)")
    assert init.returncode == 0, init.stderr
    r = subprocess.run([tf, "validate", "-no-color"], cwd=TF / root, capture_output=True, text=True, env=env)  # noqa: S603
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.unit
def test_demo_ui_is_never_deployed_to_aws() -> None:
    """Doc 11 §8.7 / decision: laptop tooling. No Terraform names it, `push` and ECR stay at the five services, and
    every cloud values file keeps it off (the rendered EKS manifest is checked in tests/helm)."""
    names = re.compile(r"demo[-_]ui", re.I)
    tf_files = [
        p for p in TF.rglob("*") if p.is_file() and ".terraform" not in p.parts and "tfstate" not in p.name
    ]
    assert [
        str(p.relative_to(ROOT))
        for p in tf_files
        if names.search(p.read_text(encoding="utf-8", errors="ignore"))
    ] == []

    five = ["alerts", "binding-page", "mock-carrier", "ref-client", "tower-mcp"]
    for root in (
        TF / "main.tf",
        TF / "ecr" / "main.tf",
    ):  # the ECR repositories and the images Terraform reads
        m = re.search(r"services\s*=\s*toset\(\[([^\]]*)\]\)", root.read_text(encoding="utf-8"))
        assert m, root
        assert sorted(re.findall(r'"([^"]+)"', m.group(1))) == five, root
    vars_mk = (ROOT / "mk" / "vars.mk").read_text(encoding="utf-8")
    services = re.search(r"^SERVICES\s*:=\s*(.+)$", vars_mk, re.M)
    assert services and sorted(services.group(1).split()) == five
    push = (ROOT / "scripts" / "push_images.py").read_text(encoding="utf-8")
    listed = re.search(r"^SERVICES\s*=\s*\[([^\]]*)\]", push, re.M)
    assert listed and sorted(re.findall(r'"([^"]+)"', listed.group(1))) == five
    build_mk = (ROOT / "mk" / "build.mk").read_text(encoding="utf-8")
    recipe = build_mk.split("\npush:", 1)[1].split("\nsbom:", 1)[0]
    assert "IMAGES" not in recipe and not names.search(recipe)  # push never iterates the six local images

    helm = ROOT / "deploy" / "helm"
    for values in (helm / "umbrella" / "values.yaml", helm / "umbrella" / "values-eks.yaml"):
        assert yaml.safe_load(values.read_text(encoding="utf-8"))["demo-ui"]["enabled"] is False, values
    assert (
        yaml.safe_load((helm / "demo-ui" / "values-eks.yaml").read_text(encoding="utf-8"))["enabled"] is False
    )
    deps = yaml.safe_load((helm / "umbrella" / "Chart.yaml").read_text(encoding="utf-8"))["dependencies"]
    assert next(d for d in deps if d["name"] == "demo-ui")["condition"] == "demo-ui.enabled"


@pytest.mark.unit
def test_web_chat_names_match_deployment_agentcore_section_1() -> None:
    """deployment-agentcore.md §1 "Web chat — Terraform names" (prompt 20) is the contract for these names."""
    variables = (TF / "variables.tf").read_text()
    for name, default in [
        ("enable_web_chat", "true"),
        ("cognito_domain_prefix", '""'),
        ("mock_assume_mobile_data", "true"),
        ("binding_session_ttl_s", "86400"),
    ]:
        block = variables.split(f'variable "{name}" {{', 1)[1].split("\n}\n", 1)[0]
        assert re.search(rf"default\s*=\s*{re.escape(default)}\s*$", block, re.M), name
    assert 'variable "enable_ref_client_runtime"' not in variables  # replaced by enable_web_chat
    outputs = (TF / "outputs.tf").read_text()
    for name in (
        "cognito_pool_id",
        "cognito_client_id",
        "cognito_issuer",
        "cognito_jwks_url",
        "cognito_hosted_ui_url",
        "web_chat_url",
        "web_chat_bucket",
        "web_chat_distribution_id",
        "agent_url",
        "agent_runtime_arn",
    ):
        assert f'output "{name}"' in outputs, name
    assert 'output "ref_client_runtime_arn"' not in outputs
    cognito = (TF / "modules" / "cognito" / "outputs.tf").read_text()
    for name in ("pool_id", "client_id", "issuer", "jwks_url", "discovery_url", "hosted_ui_url"):
        assert f'output "{name}"' in cognito, name
    web = (TF / "modules" / "web_chat" / "outputs.tf").read_text()
    for name in ("page_url", "agent_url", "bucket", "distribution_id"):
        assert f'output "{name}"' in web, name
    example = (TF / "envs" / "aws.tfvars.example").read_text()
    for name in (
        "enable_web_chat",
        "cognito_domain_prefix",
        "mock_assume_mobile_data",
        "binding_session_ttl_s",
    ):
        assert re.search(rf"^{name}\s*=", example, re.M), name


@pytest.mark.unit
def test_cognito_client_is_pkce_without_a_secret() -> None:
    main = (TF / "modules" / "cognito" / "main.tf").read_text()
    assert "generate_secret                      = false" in main
    assert '["code"]' in main and '["openid"]' in main
    assert "allow_admin_create_user_only = true" in main
    root = (TF / "main.tf").read_text()
    # Tower's env and both authorizers come from the pool unless overridden; the web-chat client is always allowed
    assert "module.cognito.jwks_url" in root and "module.cognito.issuer" in root
    assert "concat([module.cognito.client_id], var.tower_jwt_allowed_clients)" in root
    assert 'TOWER_JWT_CLIENT_IDS   = join(",", local.jwt_clients)' in root


@pytest.mark.unit
def test_agent_runtime_has_its_own_narrow_role_and_tower_url() -> None:
    """C5 / 09 §5: the agent never reads tables, keys or the Gateway; its env names Tower as TOWER_URL."""
    main = (TF / "modules" / "agentcore_runtime" / "main.tf").read_text()
    agent_policy = main.split('data "aws_iam_policy_document" "agent" {', 1)[1].split('\nresource "', 1)[0]
    assert "dynamodb:" not in agent_policy and "kms:" not in agent_policy
    assert "InvokeGateway" not in agent_policy and "bedrock:InvokeModel" in agent_policy
    runtime = main.split('resource "aws_bedrockagentcore_agent_runtime" "ref_client" {', 1)[1]
    assert "role_arn           = aws_iam_role.agent[0].arn" in runtime
    assert 'server_protocol = "HTTP"' in runtime and 'request_header_allowlist = ["Authorization"]' in runtime
    assert "TOWER_URL " in runtime and "TOWER_MCP_URL" not in main
    assert 'REF_AGENT                 = "bedrock"' in runtime
    tower_policy = main.split('data "aws_iam_policy_document" "runtime" {', 1)[1].split('\nresource "', 1)[0]
    assert "bedrock:InvokeModel" not in tower_policy  # Tower's role has no model access (rule 1)


@pytest.mark.unit
def test_web_chat_module_shape() -> None:
    main = (TF / "modules" / "web_chat" / "main.tf").read_text()
    assert "block_public_policy     = true" in main and "restrict_public_buckets = true" in main
    assert 'resource "aws_cloudfront_origin_access_control" "page"' in main
    assert 'default_root_object = "index.html"' in main
    assert 'authorization_type = "NONE"' in main and "allow_origins = [local.page_origin]" in main
    assert (
        'allow_headers = ["authorization", "content-type", "x-amzn-bedrock-agentcore-runtime-session-id"]'
        in main
    )
    assert "AGENT_INVOKE_URL = var.agent_invoke_url" in main
    assert 'runtime          = "python3.12"' in main and '["arm64"]' in main
    mock = (TF / "modules" / "mock_carrier" / "main.tf").read_text()
    assert "MOCK_ASSUME_MOBILE_DATA" in mock and "MOCK_ASSUME_CLIENT_ID" in mock
    assert "SESSION_TTL_S          = tostring(var.binding_session_ttl_s)" in (TF / "main.tf").read_text()
