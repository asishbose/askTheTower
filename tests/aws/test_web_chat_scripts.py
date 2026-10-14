"""Prompt 20 scripts, offline: `cognito_user.py` against a fake Cognito client, and `aws_seed.py`'s user map
(`--user name=sub`, the `artifacts/cognito-users.json` fallback) against moto. Nothing here calls AWS.

deployment-agentcore.md step 8/10: the AWS rows are written under the Cognito subs, never `user-asish` /
`user-mom`; without a user map the consent rows are skipped and the script exits 0; Asish's line is left unbound
unless `--bind-asish` (bind-and-alert-flows Flow 1 is filmed on AWS)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.aws.conftest import ROOT
from tests.privacy.patterns import phone_hits

ASISH_SUB = "8b1f2e3a-aaaa-4bbb-8ccc-0d0e0f0a0b0c"
MOM_SUB = "c4d5e6f7-dddd-4eee-8fff-1a1b1c1d1e1f"
OUTPUTS = {
    "region": "us-east-1",
    "table_prefix": "att-dev-",
    "kms_key_arn": "arn:aws:kms:us-east-1:acct:key/main",
    "kms_hmac_key_arn": "arn:aws:kms:us-east-1:acct:key/hmac",
    "mock_cluster_name": "att-dev-mock",
    "mock_service_name": "mock-carrier",
    "mock_container_name": "mock-carrier",
    "binding_url": "https://dexample.cloudfront.net",
    "tower_mcp_url": "https://tower.example/invocations",
    "cognito_pool_id": "us-east-1_TestPool",
}


def load(name: str) -> ModuleType:
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"script_{name}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


aws_seed = load("aws_seed")
cognito_user = load("cognito_user")


@pytest.fixture
def outputs(tmp_path: Path) -> Path:
    p = tmp_path / "tf-outputs.json"
    p.write_text(json.dumps({k: {"value": v, "sensitive": False} for k, v in OUTPUTS.items()}))
    return p


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — the repo's own script, fixed arguments
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False
    )


# --- aws_seed: the user map ------------------------------------------------------------------------------------
@pytest.mark.unit
def test_user_pairs_win_over_the_json(tmp_path: Path) -> None:
    f = tmp_path / "users.json"
    f.write_text(json.dumps({"asish": "a" * 36, "mom": "b" * 36, "stranger": "c" * 36}))
    assert aws_seed.parse_users([f"asish={ASISH_SUB}", f"mom={MOM_SUB}"], f) == {
        "asish": ASISH_SUB,
        "mom": MOM_SUB,
    }
    assert aws_seed.parse_users([], f) == {
        "asish": "a" * 36,
        "mom": "b" * 36,
    }  # json fallback; unknown names dropped
    assert aws_seed.parse_users([], tmp_path / "absent.json") == {}


@pytest.mark.unit
@pytest.mark.parametrize("pair", ["mom=user-mom", "asish=user-asish", "mom=", "mom", "mom=bad sub!"])
def test_local_ids_and_junk_are_refused(pair: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        aws_seed.parse_users([pair], tmp_path / "absent.json")


@pytest.mark.unit
def test_no_user_map_skips_the_consent_rows_and_exits_0(outputs: Path, tmp_path: Path) -> None:
    r = run(
        "scripts/aws_seed.py",
        "--outputs",
        str(outputs),
        "--dry-run",
        "--users-file",
        str(tmp_path / "none.json"),
    )
    assert r.returncode == 0, r.stderr
    assert "/_admin/scenarios/load" in r.stdout  # the mock is still reloaded
    assert "make cognito-users" in r.stdout and "consent rows skipped" in r.stdout
    assert not phone_hits(r.stdout)


@pytest.mark.unit
def test_dry_run_with_subs_names_the_rows_not_the_ids(outputs: Path, tmp_path: Path) -> None:
    r = run(
        "scripts/aws_seed.py",
        "--outputs",
        str(outputs),
        "--dry-run",
        "--skip-mock",
        "--user",
        f"asish={ASISH_SUB}",
        "--user",
        f"mom={MOM_SUB}",
    )
    assert r.returncode == 0, r.stderr
    assert "would seed users asish, mom" in r.stdout and ASISH_SUB not in r.stdout
    assert not phone_hits(r.stdout)


# --- aws_seed: the rows (moto) ---------------------------------------------------------------------------------
@pytest.fixture
def store(moto_dynamodb: Any) -> Any:
    from tower_consent import Store, tables

    s = Store(moto_dynamodb, prefix=f"w{uuid.uuid4().hex[:8]}-")
    s.ensure_tables()
    yield s
    for t in tables.TABLES:
        moto_dynamodb.delete_table(TableName=s.name(t))


def demo_numbers() -> tuple[str, str]:
    return aws_seed.demo_numbers("demo")


@pytest.mark.integration
def test_subs_are_written_into_lines_and_grants_and_asish_is_left_unbound(store: Any) -> None:
    from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, list_grants, list_lines, resolve

    hasher, cipher = LocalLineIdHasher(b"k" * 32), LocalMsisdnCipher(b"m" * 32)
    asish, mom = demo_numbers()
    users = {"asish": ASISH_SUB, "mom": MOM_SUB}
    now = datetime(2026, 10, 9, tzinfo=UTC)
    done = aws_seed.seed_people(store, hasher, cipher, users, asish_e164=asish, mom_e164=mom, now=now)
    assert "line asish: left unbound (the web chat bind story; --bind-asish to bind it)" in done
    (mom_line,) = list_lines(store, MOM_SUB)
    assert mom_line.owner_user_id == MOM_SUB
    assert list_lines(store, ASISH_SUB) == []
    grants = list_grants(store, mom_line.line_id)
    assert [(g.grantee_user_id, g.alias) for g in grants] == [(ASISH_SUB, "mom")]
    assert resolve(store, ASISH_SUB, "self").view.bound is False  # Flow 1 starts at NOT_BOUND
    assert resolve(store, ASISH_SUB, "mom").view.bound is True
    assert list_lines(store, "user-mom") == [] and list_lines(store, "user-asish") == []
    assert not any(phone_hits(x) for x in done)

    again = aws_seed.seed_people(
        store, hasher, cipher, users, asish_e164=asish, mom_e164=mom, now=now, bind_asish=True
    )
    assert "grant mom → asish watch: exists" in again and "line asish: bound" in again
    assert [line.owner_user_id for line in list_lines(store, ASISH_SUB)] == [ASISH_SUB]


@pytest.mark.integration
def test_only_mom_known_binds_her_line_and_skips_the_grant(store: Any) -> None:
    from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, list_grants, list_lines

    asish, mom = demo_numbers()
    done = aws_seed.seed_people(
        store,
        LocalLineIdHasher(b"k" * 32),
        LocalMsisdnCipher(b"m" * 32),
        {"mom": MOM_SUB},
        asish_e164=asish,
        mom_e164=mom,
        now=datetime(2026, 10, 9, tzinfo=UTC),
    )
    (line,) = list_lines(store, MOM_SUB)
    assert list_grants(store, line.line_id) == [] and done == ["user mom", "line mom: bound"]


# --- cognito_user ----------------------------------------------------------------------------------------------
class FakeCognito:
    class exceptions:  # noqa: N801 — mirrors boto3's client.exceptions namespace
        class UsernameExistsException(Exception):
            pass

        class UserNotFoundException(Exception):
            pass

    def __init__(self) -> None:
        self.users: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def admin_create_user(self, **kw: Any) -> None:
        self.calls.append(("create", kw))
        if kw["Username"] in self.users:
            raise self.exceptions.UsernameExistsException()
        self.users[kw["Username"]] = {"sub": str(uuid.uuid5(uuid.NAMESPACE_DNS, kw["Username"]))}

    def admin_set_user_password(self, **kw: Any) -> None:
        self.calls.append(("password", kw))
        self.users[kw["Username"]]["password"] = kw["Password"]

    def admin_get_user(self, **kw: Any) -> dict[str, Any]:
        if kw["Username"] not in self.users:
            raise self.exceptions.UserNotFoundException()
        return {"UserAttributes": [{"Name": "sub", "Value": self.users[kw["Username"]]["sub"]}]}

    def admin_delete_user(self, **kw: Any) -> None:
        if self.users.pop(kw["Username"], None) is None:
            raise self.exceptions.UserNotFoundException()


@pytest.mark.unit
def test_create_sub_delete(
    outputs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    password = "Not-A-Real-Pw-" + uuid.uuid4().hex
    monkeypatch.setenv("COGNITO_PASSWORD_ASISH", password)
    users_file = tmp_path / "cognito-users.json"
    fake = FakeCognito()
    args = ["--outputs", str(outputs)]
    assert cognito_user.main(["create", "asish", *args], client=fake, users_file=users_file) == 0
    sub = fake.users["asish"]["sub"]
    assert json.loads(users_file.read_text()) == {"asish": sub}
    create = next(kw for c, kw in fake.calls if c == "create")
    assert create == {"UserPoolId": "us-east-1_TestPool", "Username": "asish", "MessageAction": "SUPPRESS"}
    pw = next(kw for c, kw in fake.calls if c == "password")
    assert pw["Permanent"] is True and pw["Password"] == password
    # re-run: same sub, password reset, no error
    assert cognito_user.main(["create", "asish", *args], client=fake, users_file=users_file) == 0
    assert json.loads(users_file.read_text()) == {"asish": sub}
    assert cognito_user.main(["sub", "asish", *args], client=fake, users_file=users_file) == 0
    out = capsys.readouterr().out
    assert sub in out and password not in out and password not in users_file.read_text()
    assert cognito_user.main(["delete", "asish", *args], client=fake, users_file=users_file) == 0
    assert json.loads(users_file.read_text()) == {} and "asish" not in fake.users


@pytest.mark.unit
def test_password_from_env_or_prompt_never_empty() -> None:
    assert cognito_user.password_for("mom", {"COGNITO_PASSWORD_MOM": "x"}, prompt=lambda _: "y") == "x"
    assert cognito_user.password_for("mom", {}, prompt=lambda _: "typed") == "typed"
    with pytest.raises(SystemExit):
        cognito_user.password_for("mom", {}, prompt=lambda _: "")


@pytest.mark.unit
def test_bad_name_and_missing_pool_are_refused(outputs: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cognito_user.main(["sub", "Mom!", "--outputs", str(outputs)], client=FakeCognito())
    no_pool = tmp_path / "o.json"
    no_pool.write_text(json.dumps({"region": {"value": "us-east-1"}}))
    with pytest.raises(SystemExit, match="cognito_pool_id"):
        cognito_user.main(["sub", "mom", "--outputs", str(no_pool)], client=FakeCognito())


@pytest.mark.unit
def test_the_users_file_is_gitignored() -> None:
    r = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "-q", "artifacts/cognito-users.json"],  # noqa: S607
        cwd=ROOT,
        check=False,
    )
    assert r.returncode == 0


# --- web_chat_sync ---------------------------------------------------------------------------------------------
web_chat_sync = load("web_chat_sync")
WEB_OUTPUTS = {
    **OUTPUTS,
    "cognito_hosted_ui_url": "https://att-dev-abc123.auth.us-east-1.amazoncognito.com",
    "cognito_client_id": "exampleclientid",
    "web_chat_url": "https://dpage.cloudfront.net/",
    "agent_url": "https://abc.lambda-url.us-east-1.on.aws/invocations",
    "web_chat_bucket": "att-dev-web-chat-x",
    "web_chat_distribution_id": "EDISTRIBUTION",
}


@pytest.mark.unit
def test_config_js_is_rendered_from_the_outputs() -> None:
    js = web_chat_sync.render_config(WEB_OUTPUTS)
    cfg = json.loads(js.split("=", 1)[1].strip().rstrip(";"))
    assert cfg == {
        "COGNITO_DOMAIN": "https://att-dev-abc123.auth.us-east-1.amazoncognito.com",
        "CLIENT_ID": "exampleclientid",
        "REDIRECT_URI": "https://dpage.cloudfront.net/",  # = the Hosted UI callback URL, slash included
        "AGENT_URL": "https://abc.lambda-url.us-east-1.on.aws/invocations",
        "BINDING_BASE_URL": "https://dexample.cloudfront.net",
    }
    with pytest.raises(SystemExit, match="agent_url"):
        web_chat_sync.render_config({k: v for k, v in WEB_OUTPUTS.items() if k != "agent_url"})


@pytest.mark.unit
def test_sync_uploads_the_page_and_invalidates() -> None:
    class Recorder:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def put_object(self, **kw: Any) -> None:
            self.calls.append(kw)

        def create_invalidation(self, **kw: Any) -> None:
            self.calls.append(kw)

    s3, cf = Recorder(), Recorder()
    web_chat_sync.sync(WEB_OUTPUTS, dry_run=False, s3=s3, cloudfront=cf)
    keys = {c["Key"]: c for c in s3.calls}
    assert set(keys) == {"index.html", "app.js", "styles.css", "config.js"}
    assert all(c["Bucket"] == "att-dev-web-chat-x" for c in s3.calls)
    assert keys["config.js"]["CacheControl"] == "no-cache" and keys["index.html"]["ContentType"].startswith(
        "text/html"
    )
    assert b"LOCAL_BEARER" not in keys["config.js"]["Body"]
    (inv,) = cf.calls
    assert inv["DistributionId"] == "EDISTRIBUTION" and inv["InvalidationBatch"]["Paths"]["Items"] == ["/*"]


@pytest.mark.unit
def test_dry_run_and_url(tmp_path: Path) -> None:
    p = tmp_path / "o.json"
    p.write_text(json.dumps({k: {"value": v} for k, v in WEB_OUTPUTS.items()}))
    r = run("scripts/web_chat_sync.py", "--outputs", str(p), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "would upload s3://att-dev-web-chat-x/config.js" in r.stdout and "would invalidate /*" in r.stdout
    assert not phone_hits(r.stdout)
    r = run("scripts/web_chat_sync.py", "--outputs", str(p), "--url")
    assert r.stdout.strip() == "https://dpage.cloudfront.net/"


# --- the local seed is unchanged (prompt 20 acceptance: local seeds keep user-asish / user-mom) -------------------
@pytest.mark.unit
def test_local_compose_seed_keeps_the_local_user_ids() -> None:
    spec = importlib.util.spec_from_file_location(
        "compose_seed", ROOT / "deploy" / "compose" / "seed" / "seed.py"
    )
    assert spec and spec.loader
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    assert ("user-asish", "phone-asish") in seed.PEOPLE and ("user-mom", "phone-mom") in seed.PEOPLE
    assert ("user-mom", "user-asish", "mom") in seed.GRANTS
    assert all(owner.startswith("user-") and grantee.startswith("user-") for owner, grantee, _ in seed.GRANTS)


@pytest.mark.integration
def test_a_line_held_by_a_stale_sub_is_rebound_and_a_real_conflict_is_not(store: Any) -> None:
    """`make cognito-users` re-created the users after an earlier seed: Mom's line still names the old sub. The
    re-seed clears that orphan (and its grant) and binds the current sub; a line held by the *other* demo person
    is a real conflict and stays as it is."""
    from tower_consent import LocalLineIdHasher, LocalMsisdnCipher, list_grants, list_lines

    hasher, cipher = LocalLineIdHasher(b"k" * 32), LocalMsisdnCipher(b"m" * 32)
    asish, mom = demo_numbers()
    now = datetime(2026, 10, 14, tzinfo=UTC)
    stale = {"asish": ASISH_SUB, "mom": "stale-mom-sub-0000"}
    aws_seed.seed_people(store, hasher, cipher, stale, asish_e164=asish, mom_e164=mom, now=now)
    users = {"asish": ASISH_SUB, "mom": MOM_SUB}
    done = aws_seed.seed_people(store, hasher, cipher, users, asish_e164=asish, mom_e164=mom, now=now, bind_asish=True)
    assert any(d.startswith("line mom: rebound") for d in done), done
    (line,) = list_lines(store, MOM_SUB)
    assert list_lines(store, "stale-mom-sub-0000") == []
    assert [(g.grantee_user_id, g.alias) for g in list_grants(store, line.line_id)] == [(ASISH_SUB, "mom")]
    assert "line asish: bound" in done and not any(phone_hits(x) for x in done)

    swapped = aws_seed.seed_people(  # Asish's number under Mom's name: both subs are demo users → untouched
        store, hasher, cipher, users, asish_e164=mom, mom_e164=asish, now=now, bind_asish=True
    )
    assert "line mom: bound to another user — left as is" in swapped
