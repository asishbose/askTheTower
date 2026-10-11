#!/usr/bin/env python3
"""Web chat users in the Terraform Cognito pool (`make cognito-users`, deployment-agentcore.md step 10).

    uv run python scripts/cognito_user.py create asish     # admin_create_user + permanent password; prints the sub
    uv run python scripts/cognito_user.py sub mom          # prints mom's sub
    uv run python scripts/cognito_user.py delete mom       # removes the user (and from the json)

The pool and region come from `artifacts/tf-outputs.json` (`cognito_pool_id`, `region`). The password comes from
`COGNITO_PASSWORD_<NAME>` (e.g. `COGNITO_PASSWORD_ASISH` in the root `.env`) or a prompt; it is never written to
disk or printed. Every `create`/`sub` records `{name: sub}` in `artifacts/cognito-users.json` (gitignored), which
`make seed-aws` reads (D-F: the demo rows are written under these subs). `create` on an existing user resets its
password and reports the same sub. The pool sends no invitation e-mail (`MessageAction=SUPPRESS`).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tf_outputs import ROOT, load_outputs  # noqa: E402

USERS_FILE = ROOT / "artifacts" / "cognito-users.json"
NAME = re.compile(r"[a-z][a-z0-9-]{1,31}")


def password_for(name: str, env: dict[str, str] | None = None, prompt: Any = getpass.getpass) -> str:
    e = os.environ if env is None else env
    value = e.get(f"COGNITO_PASSWORD_{name.upper().replace('-', '_')}")
    if value:
        return value
    value = prompt(f"password for {name} (not echoed, not stored): ")
    if not value:
        raise SystemExit(f"no password for {name}: set COGNITO_PASSWORD_{name.upper()} or type one")
    return str(value)


def sub_of(user: dict[str, Any]) -> str:
    attrs = user.get("UserAttributes") or user.get("Attributes") or []
    for a in attrs:
        if a.get("Name") == "sub":
            return str(a["Value"])
    raise SystemExit("Cognito returned a user without a sub")


def create(client: Any, pool: str, name: str, password: str) -> str:
    try:
        client.admin_create_user(UserPoolId=pool, Username=name, MessageAction="SUPPRESS")
    except client.exceptions.UsernameExistsException:
        pass  # re-run: keep the user (and its sub), reset the password below
    client.admin_set_user_password(UserPoolId=pool, Username=name, Password=password, Permanent=True)
    return sub(client, pool, name)


def sub(client: Any, pool: str, name: str) -> str:
    return sub_of(client.admin_get_user(UserPoolId=pool, Username=name))


def delete(client: Any, pool: str, name: str) -> None:
    try:
        client.admin_delete_user(UserPoolId=pool, Username=name)
    except client.exceptions.UserNotFoundException:
        pass


def read_users(path: Path = USERS_FILE) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text("utf-8"))
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def write_users(users: dict[str, str], path: Path = USERS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(users.items())), indent=2) + "\n", encoding="utf-8")


def cognito_client(region: str) -> Any:
    import boto3

    return boto3.client("cognito-idp", region_name=region)


def main(argv: list[str] | None = None, *, client: Any = None, users_file: Path = USERS_FILE) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["create", "delete", "sub"])
    ap.add_argument("name", help="asish | mom (lower case; the Cognito user name)")
    ap.add_argument("--outputs", type=Path, default=None)
    args = ap.parse_args(argv)
    if not NAME.fullmatch(args.name):
        raise SystemExit(f"bad user name {args.name!r}: lower-case letters, digits and '-'")
    outputs = load_outputs(args.outputs)
    pool = outputs.get("cognito_pool_id")
    if not pool:
        raise SystemExit(
            "no cognito_pool_id in the Terraform outputs: run `make deploy` (modules/cognito) first"
        )
    client = client or cognito_client(str(outputs["region"]))
    users = read_users(users_file)
    if args.action == "delete":
        delete(client, str(pool), args.name)
        users.pop(args.name, None)
        write_users(users, users_file)
        print(f"{args.name}: deleted")
        return 0
    found = (
        create(client, str(pool), args.name, password_for(args.name))
        if args.action == "create"
        else sub(client, str(pool), args.name)
    )
    users[args.name] = found
    write_users(users, users_file)
    print(
        f"{args.name}: sub {found} → {users_file.relative_to(ROOT) if users_file.is_relative_to(ROOT) else users_file}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
