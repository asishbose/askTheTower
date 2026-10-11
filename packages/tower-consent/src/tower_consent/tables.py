"""The six DynamoDB tables as data — the contract with Terraform (13) and DynamoDB Local (`ensure_tables`).

Keys follow docs/architecture/components/04 §4 and 07 §2; TTL/PITR follow components/10 §2. Nothing here talks to
AWS. `python -m tower_consent.tables --terraform` prints the definitions as HCL-ready JSON.

No key or index attribute is ever a phone number: `line_id` is the HMAC (crypto.LineIdHasher).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Literal

AttrType = Literal["S", "N", "B"]


@dataclass(frozen=True)
class Key:
    name: str
    type: AttrType = "S"


@dataclass(frozen=True)
class Index:
    """A global secondary index. Projection is ALL: every GSI here serves a hot or near-hot read."""

    name: str
    hash_key: Key
    range_key: Key | None = None


@dataclass(frozen=True)
class Table:
    name: str  # logical name; the physical name is prefix + name
    hash_key: Key
    range_key: Key | None = None
    indexes: tuple[Index, ...] = field(default_factory=tuple)
    ttl_attribute: str | None = None
    pitr: bool = False

    def physical_name(self, prefix: str = "") -> str:
        return f"{prefix}{self.name}"

    def attribute_definitions(self) -> list[Key]:
        seen: dict[str, Key] = {}
        for k in (self.hash_key, self.range_key):
            if k is not None:
                seen.setdefault(k.name, k)
        for ix in self.indexes:
            for k in (ix.hash_key, ix.range_key):
                if k is not None:
                    seen.setdefault(k.name, k)
        return list(seen.values())


# --- the six tables ---------------------------------------------------------------------------------------

USERS = Table(name="Users", hash_key=Key("user_id"))

LINES = Table(
    name="Lines",
    hash_key=Key("line_id"),
    # resolve("self"): Query by_owner, Limit 1, newest binding first.
    indexes=(Index("by_owner", Key("owner_user_id"), Key("bound_at")),),
)

GRANTS = Table(
    name="Grants",
    hash_key=Key("line_id"),
    range_key=Key("grantee_grant"),  # "<grantee_user_id>#<grant>"
    # resolve(alias), list_granted_to(), alias-collision check: Query by_grantee.
    indexes=(
        Index("by_grantee", Key("grantee_user_id"), Key("line_grant")),
    ),  # line_grant = "<line_id>#<grant>"
)

WATCHES = Table(
    name="Watches",
    hash_key=Key("line_id"),
    range_key=Key("watcher_user_id"),
    # the poller (06 §1): every watch of one profile.
    indexes=(Index("by_profile", Key("profile"), Key("line_id")),),
)

AUDIT = Table(
    name="Audit",
    hash_key=Key("line_id"),
    range_key=Key("ts_seq"),  # "ts#seq" (07 §2)
    ttl_attribute="ttl",
    pitr=True,
)

BIND_TOKENS = Table(name="BindTokens", hash_key=Key("token"), ttl_attribute="expires_at")

TABLES: tuple[Table, ...] = (USERS, LINES, GRANTS, WATCHES, AUDIT, BIND_TOKENS)
BY_NAME: dict[str, Table] = {t.name: t for t in TABLES}


# --- renderers --------------------------------------------------------------------------------------------


def create_table_kwargs(table: Table, prefix: str = "") -> dict[str, Any]:
    """`CreateTable` arguments (on-demand billing) — used by `store.ensure_tables()` against DynamoDB Local."""
    key_schema = [{"AttributeName": table.hash_key.name, "KeyType": "HASH"}]
    if table.range_key:
        key_schema.append({"AttributeName": table.range_key.name, "KeyType": "RANGE"})
    kwargs: dict[str, Any] = {
        "TableName": table.physical_name(prefix),
        "KeySchema": key_schema,
        "AttributeDefinitions": [
            {"AttributeName": k.name, "AttributeType": k.type} for k in table.attribute_definitions()
        ],
        "BillingMode": "PAY_PER_REQUEST",
    }
    if table.indexes:
        gsis = []
        for ix in table.indexes:
            ks = [{"AttributeName": ix.hash_key.name, "KeyType": "HASH"}]
            if ix.range_key:
                ks.append({"AttributeName": ix.range_key.name, "KeyType": "RANGE"})
            gsis.append({"IndexName": ix.name, "KeySchema": ks, "Projection": {"ProjectionType": "ALL"}})
        kwargs["GlobalSecondaryIndexes"] = gsis
    return kwargs


def terraform_definitions(prefix: str = "") -> dict[str, Any]:
    """HCL-ready JSON: one object per table, shaped for an `aws_dynamodb_table` `for_each` (prompt 13)."""
    out: dict[str, Any] = {}
    for t in TABLES:
        out[t.name] = {
            "name": t.physical_name(prefix),
            "billing_mode": "PAY_PER_REQUEST",
            "hash_key": t.hash_key.name,
            "range_key": t.range_key.name if t.range_key else None,
            "attributes": [{"name": k.name, "type": k.type} for k in t.attribute_definitions()],
            "global_secondary_indexes": [
                {
                    "name": ix.name,
                    "hash_key": ix.hash_key.name,
                    "range_key": ix.range_key.name if ix.range_key else None,
                    "projection_type": "ALL",
                }
                for ix in t.indexes
            ],
            "ttl": {"enabled": t.ttl_attribute is not None, "attribute_name": t.ttl_attribute or ""},
            "point_in_time_recovery": t.pitr,
        }
    return {"tables": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tower_consent.tables", description=__doc__)
    ap.add_argument("--terraform", action="store_true", help="print HCL-ready JSON for prompt 13")
    ap.add_argument("--prefix", default="", help="physical table-name prefix (e.g. tower-dev-)")
    args = ap.parse_args(argv)
    if args.terraform:
        json.dump(terraform_definitions(args.prefix), sys.stdout, indent=2)
    else:
        for t in TABLES:
            json.dump(create_table_kwargs(t, args.prefix), sys.stdout, indent=2)
            sys.stdout.write("\n")
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
