"""Prompt 22 scripts, offline (fakes and moto; nothing here calls AWS):

- `render_env.py` (10 §2.1): closed → no token key and no SSM call; open → the URL keys from the outputs and
  `DEMO_ADMIN_BEARER` from SSM (decision 1, the only bearer written); file mode 0600; no token on stdout.
- `aws_seed.py` (08 §3, doc 11 §2): the in-task load command reads `MOCK_ADMIN_TOKEN` from the task's environment
  (no token literal); a completed non-dry run writes `artifacts/seed-aws.json` with names only; a dry run writes
  nothing.
- `web_chat_sync.py` (doc 11 §2): a real sync writes `artifacts/web-chat-sync.json`; a dry run writes nothing.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tests.aws.conftest import ROOT
from tests.privacy.patterns import phone_hits

ASISH_SUB = "8b1f2e3a-aaaa-4bbb-8ccc-0d0e0f0a0b0c"
MOM_SUB = "c4d5e6f7-dddd-4eee-8fff-1a1b1c1d1e1f"
TOKENS = {"/att-dev/demo-admin/demo-admin-bearer": "demoadminbearerAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}
OLD_KEYS = {"MOCK_ADMIN_TOKEN", "ALERTS_INTERNAL_BEARER", "BIND_ADMIN_TOKEN", "INTERNAL_BEARER"}
API = "https://abc123.execute-api.us-east-1.amazonaws.com"
BASE_OUTPUTS: dict[str, Any] = {
    "region": "us-east-1",
    "tower_mcp_url": "https://tower.example/invocations",
    "binding_url": "https://dexample.cloudfront.net",
    "hooks_base_url": "https://dexample.cloudfront.net",
    "web_chat_url": "https://dpage.cloudfront.net/",
    "table_prefix": "att-dev-",
    "kms_key_arn": "arn:aws:kms:us-east-1:acct:key/main",
    "kms_hmac_key_arn": "arn:aws:kms:us-east-1:acct:key/hmac",
    "carrier_backend": "mock",
    "mock_cluster_name": "att-dev-mock",
    "mock_service_name": "mock-carrier",
    "mock_container_name": "mock-carrier",
    "mock_admin_url": None,
    "alerts_internal_url": None,
    "binding_admin_url": None,
    "demo_admin_ssm_parameters": None,
}
OPEN_OUTPUTS = {
    **BASE_OUTPUTS,
    "mock_admin_url": f"{API}/carrier-admin",
    "alerts_internal_url": API,
    "binding_admin_url": API,
    "demo_admin_ssm_parameters": {"demo-admin-bearer": "/att-dev/demo-admin/demo-admin-bearer"},
}


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"p22_{name}", ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


render_env = load("render_env")
aws_seed = load("aws_seed")
web_chat_sync = load("web_chat_sync")


def outputs_file(tmp_path: Path, values: dict[str, Any]) -> Path:
    p = tmp_path / "tf-outputs.json"
    p.write_text(json.dumps({k: {"value": v, "sensitive": False} for k, v in values.items()}))
    return p


class FakeSsm:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_parameter(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {"Parameter": {"Name": kw["Name"], "Value": TOKENS[kw["Name"]]}}


def parse(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in path.read_text().splitlines() if line and not line.startswith("#")
    )


# --- render_env ----------------------------------------------------------------------------------------------------
@pytest.mark.unit
def test_render_env_closed_has_no_token_and_never_calls_ssm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_ssm(region: str) -> Any:
        raise AssertionError("SSM must not be read while the admin path is closed")

    monkeypatch.setattr(render_env, "ssm_client", no_ssm)
    out = tmp_path / "env.aws"
    assert (
        render_env.main(
            ["--env", "aws", "--outputs", str(outputs_file(tmp_path, BASE_OUTPUTS)), "--out", str(out)]
        )
        == 0
    )
    env = parse(out)
    assert env["MOCK_URL"] == "" and env["BINDING_ADMIN_URL"] == ""
    assert (
        env["ALERTS_URL"] == BASE_OUTPUTS["hooks_base_url"]
        and env["WEB_CHAT_URL"] == BASE_OUTPUTS["web_chat_url"]
    )
    assert not ({"DEMO_ADMIN_BEARER"} | OLD_KEYS) & set(env)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert "demo admin path: closed" in capsys.readouterr().out


@pytest.mark.unit
def test_render_env_open_reads_the_bearers_from_ssm_and_prints_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ssm = FakeSsm()
    monkeypatch.setattr(render_env, "ssm_client", lambda region: ssm)
    out = tmp_path / "env.aws"
    out.write_text("stale\n")
    out.chmod(0o644)  # an existing, wider file is narrowed
    assert (
        render_env.main(
            ["--env", "aws", "--outputs", str(outputs_file(tmp_path, OPEN_OUTPUTS)), "--out", str(out)]
        )
        == 0
    )
    env = parse(out)
    assert (
        env["MOCK_URL"] == f"{API}/carrier-admin"
        and env["BINDING_ADMIN_URL"] == API
        and env["ALERTS_URL"] == API
    )
    assert env["DEMO_ADMIN_BEARER"] == TOKENS["/att-dev/demo-admin/demo-admin-bearer"]
    assert not OLD_KEYS & set(
        env
    )  # decision 1: the laptop never holds the Alerts internal bearer or a per-service one
    assert [c["Name"] for c in ssm.calls] == ["/att-dev/demo-admin/demo-admin-bearer"]
    assert all(c["WithDecryption"] is True for c in ssm.calls)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    printed = capsys.readouterr().out
    assert not any(t in printed for t in TOKENS.values())
    assert "MOCK_URL=" in printed and "open, DEMO_ADMIN_BEARER written (not shown)" in printed


@pytest.mark.unit
def test_eks_env_is_unaffected_by_admin_keys(tmp_path: Path) -> None:
    env = render_env.eks_env({**BASE_OUTPUTS, "tower_url": "https://t.example", "mock_url": "http://m"})
    assert env["MOCK_URL"] == "http://m" and "MOCK_ADMIN_TOKEN" not in env


# --- aws_seed ------------------------------------------------------------------------------------------------------
@pytest.mark.unit
def test_in_task_load_reads_the_token_from_the_task_environment() -> None:
    cmd = aws_seed.mock_load_command(BASE_OUTPUTS, "demo", "arn:task")
    code = cmd[-1]
    assert 'os.environ.get("MOCK_ADMIN_TOKEN", "")' in code and '"authorization": "Bearer " +' in code
    assert "'" not in code.removeprefix("python -c '").removesuffix("'")  # ECS Exec quoting
    assert not any(t in " ".join(cmd) for t in TOKENS.values())


@pytest.mark.unit
def test_dry_run_writes_no_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "seed-aws.json"
    rc = aws_seed.main(["--outputs", str(outputs_file(tmp_path, BASE_OUTPUTS)), "--dry-run", "--evidence", str(evidence),
                        "--users-file", str(tmp_path / "none.json")])  # fmt: skip
    assert rc == 0 and not evidence.exists()


@pytest.fixture
def store(moto_dynamodb: Any) -> Any:
    from tower_consent import Store, tables

    s = Store(moto_dynamodb, prefix=f"p{uuid.uuid4().hex[:8]}-")
    s.ensure_tables()
    yield s
    for t in tables.TABLES:
        moto_dynamodb.delete_table(TableName=s.name(t))


@pytest.mark.integration
def test_completed_run_writes_names_only_evidence(
    store: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tower_consent import LocalLineIdHasher, LocalMsisdnCipher

    monkeypatch.setattr(aws_seed, "seed_mock", lambda outputs, scenario, dry_run: True)
    monkeypatch.setattr(
        aws_seed,
        "open_store",
        lambda outputs: (store, LocalLineIdHasher(b"k" * 32), LocalMsisdnCipher(b"m" * 32)),
    )
    evidence = tmp_path / "seed-aws.json"
    rc = aws_seed.main(["--outputs", str(outputs_file(tmp_path, BASE_OUTPUTS)), "--evidence", str(evidence),
                        "--user", f"asish={ASISH_SUB}", "--user", f"mom={MOM_SUB}"])  # fmt: skip
    assert rc == 0
    text = evidence.read_text()
    doc = json.loads(text)
    assert set(doc) == {"at", "scenario", "mock_loaded", "people", "bound", "grants"}
    assert doc["mock_loaded"] is True and doc["scenario"] == "demo"
    assert (
        doc["people"] == ["asish", "mom"] and doc["bound"] == ["mom"] and doc["grants"] == ["mom>asish:watch"]
    )
    assert ASISH_SUB not in text and MOM_SUB not in text and "ln_" not in text and not phone_hits(text)
    assert datetime.fromisoformat(doc["at"]).tzinfo is not None


@pytest.mark.unit
def test_no_users_still_records_the_mock_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aws_seed, "seed_mock", lambda outputs, scenario, dry_run: True)
    evidence = tmp_path / "seed-aws.json"
    rc = aws_seed.main(["--outputs", str(outputs_file(tmp_path, BASE_OUTPUTS)), "--evidence", str(evidence),
                        "--users-file", str(tmp_path / "none.json")])  # fmt: skip
    assert rc == 0
    doc = json.loads(evidence.read_text())
    assert doc["mock_loaded"] is True and doc["people"] == [] and doc["bound"] == [] and doc["grants"] == []


# --- web_chat_sync -------------------------------------------------------------------------------------------------
WEB_OUTPUTS = {
    **BASE_OUTPUTS,
    "cognito_hosted_ui_url": "https://att-dev-abc123.auth.us-east-1.amazoncognito.com",
    "cognito_client_id": "exampleclientid",
    "agent_url": "https://abc.lambda-url.us-east-1.on.aws/invocations",
    "web_chat_bucket": "att-dev-web-chat-x",
    "web_chat_distribution_id": "EDISTRIBUTION",
}


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def put_object(self, **kw: Any) -> None:
        self.calls.append(kw)

    def create_invalidation(self, **kw: Any) -> None:
        self.calls.append(kw)


@pytest.mark.unit
def test_real_sync_writes_the_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_boto3 = SimpleNamespace(client=lambda name, **kw: Recorder())
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    evidence = tmp_path / "web-chat-sync.json"
    assert (
        web_chat_sync.main(
            ["--outputs", str(outputs_file(tmp_path, WEB_OUTPUTS)), "--evidence", str(evidence)]
        )
        == 0
    )
    doc = json.loads(evidence.read_text())
    assert doc["url"] == WEB_OUTPUTS["web_chat_url"] and doc["distribution_id"] == "EDISTRIBUTION"
    assert doc["files"] == ["app.js", "config.js", "index.html", "styles.css"]
    assert datetime.fromisoformat(doc["at"]).utcoffset() is not None  # tz-aware


@pytest.mark.unit
def test_dry_run_sync_writes_no_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "web-chat-sync.json"
    p = outputs_file(tmp_path, WEB_OUTPUTS)
    assert web_chat_sync.main(["--outputs", str(p), "--dry-run", "--evidence", str(evidence)]) == 0
    assert not evidence.exists()
