"""Helm charts and their scripts (prompt 14), offline.

Unit: render_values.py maps the EKS root's outputs onto the chart values; k8s_seed.py picks the demo lines from the
mock's state and syncs itself into the chart; the charts carry the env names compose uses; no secret-shaped value
or phone number is committed in deploy/helm. Integration (skipped without the CLI): helm lint, helm-unittest and
`helm template | kubeconform` for the kind and EKS values.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM = ROOT / "deploy" / "helm"
UMBRELLA = HELM / "umbrella"
CHARTS = ["mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client", "dynamodb-local", "demo-ui"]
TOOLS = Path.home() / "tools"


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_outputs() -> dict:
    acct = "ACCOUNT"
    repo = f"{acct}.dkr.ecr.us-east-1.amazonaws.com/att-demo"
    return {
        "region": "us-east-1",
        "cluster_name": "att-demo",
        "vpc_cidr": "10.40.0.0/16",
        "public_subnet_ids": ["subnet-a", "subnet-b"],
        "irsa_role_arns": {
            s: f"arn:aws:iam::{acct}:role/att-{s}"
            for s in ["tower-mcp", "binding-page", "alerts", "ref-client", "aws-load-balancer-controller"]
        },
        "ecr_repositories": {
            s: f"{repo}/{s}" for s in ["mock-carrier", "tower-mcp", "binding-page", "alerts", "ref-client"]
        },
        "table_prefix": "att-demo-",
        "kms_key_arn": f"arn:aws:kms:us-east-1:{acct}:key/k1",
        "kms_hmac_key_arn": f"arn:aws:kms:us-east-1:{acct}:key/k2",
        "sns_topic_arns": {"replies": f"arn:aws:sns:us-east-1:{acct}:att-demo-sms-replies"},
        "bedrock_model_id": "amazon.nova-micro-v1:0",
        "tower_host": "tower.example.org",
        "binding_host": "bind.example.org",
        "hooks_host": "hooks.example.org",
        "certificate_arn": f"arn:aws:acm:us-east-1:{acct}:certificate/c1",
    }


def helm_bin() -> str | None:
    return shutil.which("helm")


def kubeconform_bin() -> str | None:
    found = shutil.which("kubeconform")
    return found or (str(TOOLS / "kubeconform") if (TOOLS / "kubeconform").exists() else None)


# --- unit -------------------------------------------------------------------------------------------------------


@pytest.mark.unit
def test_every_chart_and_values_file_exists() -> None:
    for c in CHARTS:
        for f in ("Chart.yaml", "values.yaml", "templates/_helpers.tpl", "templates/deployment.yaml"):
            assert (HELM / c / f).exists(), f"{c}/{f}"
        assert list((HELM / "tests" / c).glob("*_test.yaml")), f"helm-unittest suite for {c}"
    for f in ("Chart.yaml", "values.yaml", "values-kind.yaml", "values-eks.yaml", "templates/seed.yaml"):
        assert (UMBRELLA / f).exists(), f
    deps = {d["name"] for d in yaml.safe_load((UMBRELLA / "Chart.yaml").read_text())["dependencies"]}
    assert deps == set(CHARTS)


@pytest.mark.unit
def test_render_values_maps_terraform_outputs() -> None:
    rv = load_script("render_values")
    v = rv.render(fake_outputs(), "abc123")
    assert v["global"]["imageTag"] == "abc123"
    assert v["global"]["storeEnv"]["TOWER_KMS_HMAC_KEY_ID"].endswith("key/k2")
    assert v["tower-mcp"]["image"]["repository"].endswith("/att-demo/tower-mcp")
    for svc in ("tower-mcp", "binding-page", "alerts", "ref-client"):
        assert v[svc]["serviceAccount"]["annotations"]["eks.amazonaws.com/role-arn"].endswith(f"att-{svc}")
    assert "serviceAccount" not in v["mock-carrier"]  # the mock calls no AWS API
    assert v["binding-page"]["env"]["BIND_REDIRECT_URI"] == "https://bind.example.org/bind/callback"
    assert v["alerts"]["env"]["HOOKS_BASE_URL"] == "https://hooks.example.org"
    assert v["alerts"]["env"]["SNS_TOPIC_ARN"].endswith("att-demo-sms-replies")
    assert v["alerts"]["networkPolicy"]["from"]["cidrs"] == ["10.40.0.0/16"]
    ann = v["tower-mcp"]["ingress"]["annotations"]
    assert ann["alb.ingress.kubernetes.io/certificate-arn"].endswith("certificate/c1")
    assert ann["alb.ingress.kubernetes.io/subnets"] == "subnet-a,subnet-b"


@pytest.mark.unit
def test_render_values_without_certificate_falls_back_to_http_and_refuses_wrong_root() -> None:
    rv = load_script("render_values")
    o = fake_outputs() | {"certificate_arn": "", "hooks_host": ""}
    v = rv.render(o, "latest")
    assert v["alerts"]["ingress"]["annotations"]["alb.ingress.kubernetes.io/listen-ports"] == '[{"HTTP":80}]'
    assert v["alerts"]["ingress"]["host"] == "tower.example.org"  # hooks share the Tower host when unset
    with pytest.raises(SystemExit, match="irsa_role_arns"):
        rv.render({k: val for k, val in fake_outputs().items() if k != "irsa_role_arns"}, "x")


@pytest.mark.unit
def test_render_values_cli_writes_the_generated_file(tmp_path: Path) -> None:
    rv = load_script("render_values")
    outputs = tmp_path / "out.json"
    outputs.write_text(json.dumps({k: {"value": v} for k, v in fake_outputs().items()}))
    target = tmp_path / "values-eks.generated.yaml"
    assert rv.main(["--outputs", str(outputs), "--out", str(target), "--tag", "t1"]) == 0
    doc = yaml.safe_load(target.read_text())
    assert doc["global"]["imageTag"] == "t1" and "ref-client" in doc


@pytest.mark.unit
def test_k8s_seed_picks_demo_lines_by_client_id() -> None:
    seed = load_script("k8s_seed")
    state = {
        "lines": {
            "a": {"msisdn": "+1555LINEA", "mobile_data_client_ids": ["phone-mom"]},
            "b": {"msisdn": "+1555LINEB", "mobile_data_client_ids": ["phone-asish"]},
        }
    }
    assert seed.demo_lines(state) == {"asish": "+1555LINEB", "mom": "+1555LINEA"}
    with pytest.raises(SystemExit, match="mom"):
        seed.demo_lines({"lines": {"b": state["lines"]["b"]}})


@pytest.mark.unit
def test_k8s_seed_sync_copies_both_scripts(tmp_path: Path) -> None:
    seed = load_script("k8s_seed")
    written = seed.sync_chart(tmp_path)
    assert {p.name for p in written} == {"k8s_seed.py", "compose_seed.py"}
    assert (tmp_path / "k8s_seed.py").read_text() == (ROOT / "scripts" / "k8s_seed.py").read_text()
    assert (tmp_path / "compose_seed.py").read_text() == (ROOT / "deploy/compose/seed/seed.py").read_text()


@pytest.mark.unit
def test_k8s_seed_rerun_dry_run_prints_commands(capsys: pytest.CaptureFixture[str]) -> None:
    seed = load_script("k8s_seed")
    assert seed.main(["--dry-run", "--release", "att", "-n", "ask-the-tower"]) == 0
    out = capsys.readouterr().out
    assert (
        "helm get hooks att" in out
        and "kubectl -n ask-the-tower wait --for=condition=complete job/att-seed" in out
    )


@pytest.mark.unit
def test_charts_use_the_compose_env_names() -> None:
    """Identical env names and ports to deploy/compose (prompt 14 step 1)."""
    compose = yaml.safe_load((ROOT / "deploy/compose/docker-compose.yml").read_text())["services"]
    pairs = {
        "mock-carrier": "mock-carrier",
        "tower-mcp": "tower-mcp",
        "binding-page": "binding-page",
        "alerts": "alerts",
        "demo-ui": "demo-ui",
    }
    store = set(yaml.safe_load((UMBRELLA / "values.yaml").read_text())["global"]["storeEnv"])
    kind_store = set(yaml.safe_load((UMBRELLA / "values-kind.yaml").read_text())["global"]["storeEnv"])
    secret_store = set(
        yaml.safe_load((UMBRELLA / "values-kind.yaml").read_text())["global"]["storeSecretEnv"]
    )
    ignore = {"SSL_CERT_FILE"}  # set by the chart only when trustCA is configured
    for chart, svc in pairs.items():
        values = yaml.safe_load((HELM / chart / "values.yaml").read_text())
        names = set(values["env"]) | set(values.get("secretEnv") or {})
        if values.get("useStoreEnv"):
            names |= store | kind_store | secret_store
        missing = {k for k in compose[svc]["environment"] if k not in names} - ignore
        assert not missing, f"{chart}: compose sets {sorted(missing)} which the chart does not"


@pytest.mark.unit
def test_web_chat_chart_uses_the_compose_env_names() -> None:
    """09 §6.3: compose `web-chat` and the ref-client chart's `web` Deployment set the same variables."""
    compose = yaml.safe_load((ROOT / "deploy/compose/docker-compose.yml").read_text())["services"]["web-chat"]
    values = yaml.safe_load((HELM / "ref-client" / "values.yaml").read_text())
    names = (
        set(values["env"]) | set(values["web"]["env"]) | set(values["secretEnv"]) | {"REF_CLIENT_HTTP_PORT"}
    )
    missing = {k for k in compose["environment"] if k not in names} - {"PYTHONUNBUFFERED"}  # set in the image
    assert not missing, f"compose web-chat sets {sorted(missing)} which the chart does not"
    assert compose["ports"] == ["127.0.0.1:${WEB_CHAT_HOST_PORT:-8083}:8080"]  # loopback only: /config.js
    assert values["web"]["ingress"]["enabled"] is False


@pytest.mark.unit
def test_no_secrets_or_numbers_committed_in_deploy_helm() -> None:
    from tests.privacy.patterns import phone_hits

    secretish = re.compile(
        r"(?i)^\s*(\w*(secret|bearer|password|token)\w*)\s*:\s*['\"]?([A-Za-z0-9+/=_-]{16,})"
    )
    allowed = {"local-dev-tower", "local-dev-binding", "local-dev-alerts"}
    for f in HELM.rglob("*"):
        if (
            not f.is_file()
            or "charts" in f.parts
            or f.suffix == ".tgz"
            or "files" in f.relative_to(HELM).parts
        ):
            continue
        text = f.read_text(errors="ignore")
        assert not phone_hits(text), f
        for m in secretish.finditer(text):
            assert m.group(3) in allowed or m.group(3).isupper(), f"{f}: {m.group(1)}"


@pytest.mark.unit
def test_every_container_is_hardened_in_the_templates() -> None:
    for c in CHARTS:
        values = yaml.safe_load((HELM / c / "values.yaml").read_text())
        assert values["podSecurityContext"]["runAsNonRoot"] is True
        sc = values["securityContext"]
        assert sc["readOnlyRootFilesystem"] is True and sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"]["drop"] == ["ALL"]
        assert values["resources"]["limits"] and values["resources"]["requests"]


# --- integration (CLI) ------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built_umbrella() -> Path:
    helm = helm_bin()
    if helm is None:
        pytest.skip("helm CLI not installed")
    load_script("k8s_seed").sync_chart()
    cmd = [helm, "dependency", "update", str(UMBRELLA), "--skip-refresh"]
    r = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    assert r.returncode == 0, r.stderr
    return UMBRELLA


@pytest.mark.integration
@pytest.mark.parametrize("chart", CHARTS)
def test_helm_lint_each_chart(chart: str) -> None:
    helm = helm_bin()
    if helm is None:
        pytest.skip("helm CLI not installed")
    r = subprocess.run([helm, "lint", "--strict", str(HELM / chart)], capture_output=True, text=True)  # noqa: S603
    assert r.returncode == 0, r.stdout + r.stderr


def render(umbrella: Path, *values: Path) -> str:
    args = ["template", "att", str(umbrella), "-n", "ask-the-tower"]
    for v in values:
        args += ["-f", str(v)]
    r = subprocess.run([helm_bin() or "helm", *args], capture_output=True, text=True)  # noqa: S603
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.integration
@pytest.mark.parametrize("target", ["kind", "eks"])
def test_umbrella_template_passes_kubeconform(built_umbrella: Path, target: str, tmp_path: Path) -> None:
    values = [built_umbrella / f"values-{target}.yaml"]
    if target == "eks":
        gen = tmp_path / "gen.yaml"
        gen.write_text(yaml.safe_dump(load_script("render_values").render(fake_outputs(), "t")))
        values.append(gen)
    manifest = render(built_umbrella, *values)
    kinds = [d["kind"] for d in yaml.safe_load_all(manifest) if d]
    if target == "kind":
        # incl. dynamodb-local and demo-ui (doc 11: kind only) and the web chat agent ref-client-web (09 §6.3)
        assert "CronJob" not in kinds and kinds.count("Deployment") == 7
    else:
        assert (
            kinds.count("CronJob") == 5 and kinds.count("Ingress") == 3 and "HorizontalPodAutoscaler" in kinds
        )
        assert not any(d["metadata"]["name"] == "dynamodb-local" for d in yaml.safe_load_all(manifest) if d)
        # doc 11 §8.7: the demo UI is never on a cloud cluster
        assert not any(d["metadata"]["name"] == "demo-ui" for d in yaml.safe_load_all(manifest) if d)
    kc = kubeconform_bin()
    if kc is None:
        pytest.skip("kubeconform not installed (PATH or ~/tools)")
    r = subprocess.run(  # noqa: S603
        [kc, "-strict", "-ignore-missing-schemas", "-summary", "-kubernetes-version", "1.31.0"],
        input=manifest,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 and ("dial tcp" in r.stdout + r.stderr or "no such host" in r.stdout + r.stderr):
        pytest.skip("kubeconform could not download schemas (network)")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.integration
def test_helm_unittest_suites(built_umbrella: Path) -> None:
    helm = helm_bin()
    assert helm
    plugins = subprocess.run([helm, "plugin", "list"], capture_output=True, text=True).stdout  # noqa: S603
    if "unittest" not in plugins:
        pytest.skip(
            "helm-unittest plugin not installed (helm plugin install https://github.com/helm-unittest/helm-unittest)"
        )
    r = subprocess.run([str(HELM / "tests" / "run.sh")], capture_output=True, text=True, env=os.environ)  # noqa: S603
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
