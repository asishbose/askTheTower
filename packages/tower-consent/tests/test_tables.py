"""tables.py is the Terraform contract: six tables, keys per 04 §4 / 07 §2, TTL on BindTokens + Audit, PITR on Audit."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from tower_consent import Store, tables


@pytest.mark.unit
def test_six_tables_with_doc_keys() -> None:
    assert [t.name for t in tables.TABLES] == ["Users", "Lines", "Grants", "Watches", "Audit", "BindTokens"]
    assert tables.LINES.hash_key.name == "line_id"
    assert (tables.GRANTS.hash_key.name, tables.GRANTS.range_key and tables.GRANTS.range_key.name) == (
        "line_id",
        "grantee_grant",
    )
    assert tables.WATCHES.range_key is not None and tables.WATCHES.range_key.name == "watcher_user_id"
    assert tables.BIND_TOKENS.ttl_attribute == "expires_at"
    assert tables.AUDIT.pitr and tables.AUDIT.ttl_attribute == "ttl"
    assert [t.name for t in tables.TABLES if t.pitr] == ["Audit"]
    assert [ix.name for ix in tables.GRANTS.indexes] == ["by_grantee"]


@pytest.mark.unit
def test_no_key_or_index_attribute_is_a_number() -> None:
    for t in tables.TABLES:
        for k in t.attribute_definitions():
            assert "msisdn" not in k.name and "e164" not in k.name and "phone" not in k.name


@pytest.mark.unit
def test_terraform_json_cli() -> None:
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "tower_consent.tables", "--terraform", "--prefix", "tower-dev-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data = json.loads(out)["tables"]
    assert set(data) == {t.name for t in tables.TABLES}
    assert data["BindTokens"]["ttl"] == {"enabled": True, "attribute_name": "expires_at"}
    assert data["Audit"]["point_in_time_recovery"] is True
    assert data["Lines"]["name"] == "tower-dev-Lines"
    assert data["Grants"]["global_secondary_indexes"][0]["hash_key"] == "grantee_user_id"


@pytest.mark.integration
def test_ensure_tables_is_idempotent(store: Store) -> None:
    assert store.ensure_tables() == []
    names = set(store.client.list_tables()["TableNames"])
    assert {store.name(t) for t in tables.TABLES} <= names
    ttl = store.client.describe_time_to_live(TableName=store.name(tables.BIND_TOKENS))
    assert ttl["TimeToLiveDescription"].get("AttributeName") == "expires_at"
