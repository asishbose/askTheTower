#!/usr/bin/env python3
"""Seed the AWS deployment (`make seed-aws`, part of `make deploy`): the same demo the local stack uses, pointed at
Terraform's outputs.

1. **Mock carrier on Fargate** — reset it to `scenarios/<scenario>.yaml` (default `demo`) through its admin API.
   The call runs *inside* the task with ECS Exec (bastion-less), whether or not the demo admin path is open:
   `aws ecs execute-command … --command "python -c '<POST 127.0.0.1:8443/_admin/scenarios/load>'"`. The task always
   has `MOCK_ADMIN_TOKEN` (08 §3, G1); the in-task code reads it from the task's own environment, so no token
   travels on the command line. Needs the AWS CLI and the Session Manager plugin.
2. **Consent store** — the demo's people under their **Cognito `sub`s** (D-F), never the local names
   `user-asish` / `user-mom`: `--user asish=<sub> --user mom=<sub>`, else `artifacts/cognito-users.json`
   (written by `make cognito-users`). Both users exist; Mom's line is bound to Mom and she has granted Asish
   `watch` as "mom" (pre-seeded, D-F). **Asish's line is left unbound** so the web chat story can be filmed as
   bind-and-alert-flows Flow 1 (NOT_BOUND → link → one tap on the phone → ask again); `--bind-asish` binds it too
   (what `make demo ENV=aws` expects). `line_id` and `msisdn_enc` are made by KMS (TOWER_ENV=aws, the keys from
   the outputs). Numbers come from the scenario file and leave only as line ids and ciphertext. Re-running is
   safe: an existing binding or grant is reported, not duplicated.
   Without a user map the consent rows are skipped with a hint and the script still exits 0, so the `make deploy`
   chain finishes before `make cognito-users` exists.

    uv run python scripts/aws_seed.py                                   # both steps (subs from the json)
    uv run python scripts/aws_seed.py --user asish=<sub> --user mom=<sub>
    uv run python scripts/aws_seed.py --dry-run                         # print what would run; touches nothing
    uv run python scripts/aws_seed.py --skip-mock                       # consent store only

A non-dry run that completes writes `artifacts/seed-aws.json` (doc 11 §2, pane 5's checklist): `{at, scenario,
mock_loaded, people, bound, grants}` — names only, never a sub, an id or a number.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tf_outputs import ROOT, load_outputs  # noqa: E402

USERS_FILE = ROOT / "artifacts" / "cognito-users.json"  # written by scripts/cognito_user.py
EVIDENCE_FILE = ROOT / "artifacts" / "seed-aws.json"  # read by the demo UI's AWS checklist (doc 11 §2)
PEOPLE = ("asish", "mom")
MOM_ALIAS = "mom"
SUB = re.compile(r"[A-Za-z0-9-]{8,128}")
NO_USERS_HINT = (
    "store: no Cognito users — run `make cognito-users`, then `make seed-aws` (consent rows skipped)"
)


def parse_users(pairs: list[str], fallback: Path = USERS_FILE) -> dict[str, str]:
    """`--user name=sub` pairs, else the json `make cognito-users` wrote, else {}. Only asish and mom count; the
    local ids (`user-…`) are refused: on AWS a user id is a Cognito `sub` (D-A, D-F)."""
    users: dict[str, str] = {}
    if pairs:
        for pair in pairs:
            name, sep, value = pair.partition("=")
            if not sep:
                raise SystemExit(f"--user wants name=sub, got {pair!r}")
            users[name.strip()] = value.strip()
    elif fallback.exists():
        data = json.loads(fallback.read_text("utf-8"))
        users = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    users = {k: v for k, v in users.items() if k in PEOPLE}
    for name, sub in users.items():
        if sub.startswith("user-") or not SUB.fullmatch(sub):
            raise SystemExit(
                f"--user {name}=…: not a Cognito sub (the local ids user-asish/user-mom never go to AWS)"
            )
    return users


def aws_cli() -> str:
    found = shutil.which("aws")
    if not found:
        raise SystemExit("the AWS CLI (and the Session Manager plugin for ECS Exec) must be on PATH")
    return found


def mock_load_command(outputs: dict[str, Any], scenario: str, task_arn: str) -> list[str]:
    if not scenario.replace("-", "").replace("_", "").isalnum():
        raise SystemExit(f"bad scenario name {scenario!r}")
    # No single quotes inside: ECS Exec passes the string to the container as `python -c '<code>'`. The bearer is
    # read inside the task from its own MOCK_ADMIN_TOKEN (08 §3), never written into this command.
    code = (
        "import json, os, urllib.request as u; "
        'r = u.Request("http://127.0.0.1:8443/_admin/scenarios/load", '
        f'data=json.dumps({{"name": "{scenario}"}}).encode(), '
        'headers={"content-type": "application/json", '
        '"authorization": "Bearer " + os.environ.get("MOCK_ADMIN_TOKEN", "")}, method="POST"); '
        "print(u.urlopen(r, timeout=10).read().decode())"
    )
    return [
        "aws", "ecs", "execute-command",
        "--region", str(outputs["region"]),
        "--cluster", str(outputs["mock_cluster_name"]),
        "--task", task_arn,
        "--container", str(outputs["mock_container_name"]),
        "--interactive",
        "--command", f"python -c '{code}'",
    ]  # fmt: skip


def mock_task_arn(outputs: dict[str, Any]) -> str:
    out = subprocess.run(  # noqa: S603
        [
            aws_cli(),
            "ecs",
            "list-tasks",
            "--region",
            str(outputs["region"]),
            "--cluster",
            str(outputs["mock_cluster_name"]),
            "--service-name",
            str(outputs["mock_service_name"]),
            "--desired-status",
            "RUNNING",
            "--output",
            "json",
        ],  # fmt: skip
        check=True,
        capture_output=True,
        text=True,
    )
    arns = json.loads(out.stdout).get("taskArns", [])
    if not arns:
        raise SystemExit("no running mock-carrier task (is the ECS service up?)")
    return str(arns[0])


def seed_mock(outputs: dict[str, Any], scenario: str, dry_run: bool) -> bool:
    """True when the scenario was loaded (a failed load raises)."""
    if not outputs.get("mock_cluster_name"):
        print("mock: carrier_backend is not mock — nothing to seed on the carrier side")
        return False
    task = "<running task arn>" if dry_run else mock_task_arn(outputs)
    cmd = mock_load_command(outputs, scenario, task)
    print("+", " ".join(shlex.quote(c) for c in cmd))
    if dry_run:
        return False
    subprocess.run([aws_cli(), *cmd[1:]], check=True)  # noqa: S603
    return True


def demo_numbers(scenario: str) -> tuple[str, str]:
    doc = yaml.safe_load((ROOT / "scenarios" / f"{scenario}.yaml").read_text("utf-8"))
    numbers = list(doc["lines"])
    if len(numbers) < 2:
        raise SystemExit(f"scenarios/{scenario}.yaml needs two lines (Asish, Mom)")
    return numbers[0], numbers[1]


def store_env(outputs: dict[str, Any]) -> dict[str, str]:
    return {
        "TOWER_ENV": "aws",
        "AWS_REGION": str(outputs["region"]),
        "TOWER_TABLE_PREFIX": str(outputs["table_prefix"]),
        "TOWER_KMS_KEY_ID": str(outputs["kms_key_arn"]),
        "TOWER_KMS_HMAC_KEY_ID": str(outputs["kms_hmac_key_arn"]),
    }


def seed_people(
    store: Any,
    hasher: Any,
    cipher: Any,
    users: dict[str, str],
    *,
    asish_e164: str,
    mom_e164: str,
    now: datetime,
    bind_asish: bool = False,
) -> list[str]:
    """The AWS demo rows under the Cognito subs. Returns what it did, one line per row, without ids or numbers."""
    from tower_consent import bind_line, ensure_user, grant
    from tower_consent import tables as T
    from tower_consent.errors import AliasCollision, GrantExists, LineOwnedByOtherUser

    done: list[str] = []
    for name in PEOPLE:
        if name in users:
            ensure_user(store, users[name], now=now)
            done.append(f"user {name}")

    demo_subs = set(users.values())

    def bind(name: str, e164: str) -> str | None:
        try:
            line = bind_line(store, hasher, cipher, users[name], e164, "auth_code", now=now)
        except LineOwnedByOtherUser:
            line_id = hasher.line_id(e164)
            owner = (store.get(T.LINES, {"line_id": line_id}) or {}).get("owner_user_id")
            if owner in demo_subs:  # the other demo person holds it: a real conflict, never overwritten
                done.append(f"line {name}: bound to another user — left as is")
                return None
            # An orphan: the owner is a Cognito sub that no longer belongs to a demo user (the pool or the user
            # was recreated by `make cognito-users` after an earlier seed). Clear the line and its grants and
            # watches, then bind it to the current sub — otherwise every re-seed after a user re-create stalls.
            purged = purge_line(store, line_id, owner)
            line = bind_line(store, hasher, cipher, users[name], e164, "auth_code", now=now)
            done.append(f"line {name}: rebound (was held by a stale user; {purged} dependent rows cleared)")
            return line.line_id
        done.append(f"line {name}: bound")
        return line.line_id

    mom_line = bind("mom", mom_e164) if "mom" in users else None
    if "asish" in users and bind_asish:
        bind("asish", asish_e164)
    elif "asish" in users:
        done.append("line asish: left unbound (the web chat bind story; --bind-asish to bind it)")
    if mom_line and "asish" in users:
        try:
            grant(store, mom_line, users["asish"], "watch", MOM_ALIAS, granted_by=users["mom"], now=now)
            done.append("grant mom → asish watch")
        except (GrantExists, AliasCollision):  # a re-run: the grant (alias "mom") is already there
            done.append("grant mom → asish watch: exists")
    return done


def purge_line(store: Any, line_id: str, owner: str | None) -> int:
    """Delete one orphaned Lines row (only while `owner` still holds it) and the Grants and Watches rows keyed by
    its line id. Audit rows stay: they are the history. Returns how many dependent rows went."""
    from tower_consent import tables as T

    removed = 0
    for table, rng in ((T.GRANTS, "grantee_grant"), (T.WATCHES, "watcher_user_id")):
        for item in store.query(table, "line_id = :l", {":l": line_id}):
            store.delete(table, {"line_id": line_id, rng: item[rng]})
            removed += 1
    store.delete(
        T.LINES, {"line_id": line_id}, condition="owner_user_id = :o", values={":o": owner}
    )
    return removed


def seed_evidence(
    store: Any, users: dict[str, str], *, scenario: str, mock_loaded: bool, now: datetime
) -> dict[str, Any]:
    """What the seed left, read back from the store: names only (no sub, line id or number)."""
    from tower_consent import list_grants, list_lines

    people = [name for name in PEOPLE if name in users]
    lines = {name: list_lines(store, users[name]) if store is not None else [] for name in people}
    grants: list[str] = []
    if lines.get("mom") and "asish" in users:
        active = list_grants(store, lines["mom"][0].line_id, include_revoked=False)
        if any(g.grantee_user_id == users["asish"] and g.grant == "watch" for g in active):
            grants.append("mom>asish:watch")
    return {
        "at": now.isoformat(),
        "scenario": scenario,
        "mock_loaded": mock_loaded,
        "people": people,
        "bound": [name for name in people if lines[name]],
        "grants": grants,
    }


def write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2) + "\n", "utf-8")


def open_store(outputs: dict[str, Any]) -> tuple[Any, Any, Any]:
    import boto3
    from tower_consent import Store, crypto_from_env

    env = store_env(outputs)
    store = Store.from_env(env)
    hasher, cipher = crypto_from_env(env, kms_client=boto3.client("kms", region_name=env["AWS_REGION"]))
    return store, hasher, cipher


def seed_store(
    outputs: dict[str, Any], scenario: str, dry_run: bool, users: dict[str, str], bind_asish: bool
) -> Any:
    """The consent rows; returns the store it wrote to (None when it wrote nothing)."""
    env = store_env(outputs)
    print(f"store: tables {env['TOWER_TABLE_PREFIX']}* in {env['AWS_REGION']}, KMS keys from outputs")
    if not users:
        print(NO_USERS_HINT)
        return None
    if dry_run:
        print(
            f"store: would seed users {', '.join(sorted(users))} (Cognito subs), Mom's line, the Mom→Asish watch "
            f"grant{' and Asish line' if bind_asish else ''}"
        )
        return None
    store, hasher, cipher = open_store(outputs)
    asish, mom = demo_numbers(scenario)
    for line in seed_people(
        store,
        hasher,
        cipher,
        users,
        asish_e164=asish,
        mom_e164=mom,
        now=datetime.now(UTC),
        bind_asish=bind_asish,
    ):
        print(f"store: {line}")
    return store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outputs", type=Path, default=None)
    ap.add_argument("--scenario", default="demo")
    ap.add_argument("--skip-mock", action="store_true")
    ap.add_argument("--skip-store", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--user", action="append", default=[], metavar="NAME=SUB", help="asish=<sub>, mom=<sub> (Cognito)"
    )
    ap.add_argument("--users-file", type=Path, default=USERS_FILE, help="fallback when no --user is given")
    ap.add_argument("--bind-asish", action="store_true", help="also bind Asish's line (make demo ENV=aws)")
    ap.add_argument(
        "--evidence", type=Path, default=EVIDENCE_FILE, help="where a completed run records itself"
    )
    args = ap.parse_args(argv)
    outputs = load_outputs(args.outputs)
    users = parse_users(args.user, args.users_file)
    mock_loaded = False if args.skip_mock else seed_mock(outputs, args.scenario, args.dry_run)
    store = None
    if not args.skip_store:
        store = seed_store(outputs, args.scenario, args.dry_run, users, args.bind_asish)
    if not args.dry_run:
        evidence = seed_evidence(
            store,
            users if store is not None else {},
            scenario=args.scenario,
            mock_loaded=mock_loaded,
            now=datetime.now(UTC),
        )
        write_evidence(args.evidence, evidence)
        print(f"evidence: {args.evidence.name} (people {', '.join(evidence['people']) or 'none'})")
    print(f"binding page: {outputs.get('binding_url')}  Tower MCP: {outputs.get('tower_mcp_url')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
