"""Thin DynamoDB wrapper over the boto3 *client* (so a test can count requests with an event hook).

Items in and out are plain python values; `TypeSerializer`/`TypeDeserializer` do the AttributeValue mapping.
Conditional-check failures become `ConditionFailed`. No caching anywhere (revocation must never lie).
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from tower_consent import tables as T
from tower_consent.errors import ConditionFailed

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

_ser = TypeSerializer()
_de = TypeDeserializer()


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, set):
        return {_plain(x) for x in v}
    return v


def to_av(item: Mapping[str, Any]) -> dict[str, Any]:
    return {k: _ser.serialize(v) for k, v in item.items()}


def from_av(item: Mapping[str, Any]) -> dict[str, Any]:
    return {k: _plain(_de.deserialize(v)) for k, v in item.items()}


def _is_conditional(e: ClientError) -> bool:
    code = e.response.get("Error", {}).get("Code", "")
    return code in ("ConditionalCheckFailedException", "TransactionCanceledException")


class Store:
    """One DynamoDB client plus a physical-name prefix. Construct with `Store.from_env()` in services."""

    def __init__(self, client: DynamoDBClient, prefix: str = "") -> None:
        self.client = client
        self.prefix = prefix

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Store:
        """`TOWER_DYNAMODB_ENDPOINT` (DynamoDB Local; unset on AWS), `AWS_REGION`, `TOWER_TABLE_PREFIX`."""
        import boto3

        env = os.environ if env is None else env
        kwargs: dict[str, Any] = {
            "region_name": env.get("AWS_REGION", env.get("AWS_DEFAULT_REGION", "us-east-1"))
        }
        if env.get("TOWER_DYNAMODB_ENDPOINT"):
            kwargs["endpoint_url"] = env["TOWER_DYNAMODB_ENDPOINT"]
        return cls(boto3.client("dynamodb", **kwargs), env.get("TOWER_TABLE_PREFIX", ""))

    def name(self, table: T.Table) -> str:
        return table.physical_name(self.prefix)

    # --- reads -------------------------------------------------------------------------------------------

    def get(
        self, table: T.Table, key: Mapping[str, Any], *, consistent: bool = True
    ) -> dict[str, Any] | None:
        resp = self.client.get_item(TableName=self.name(table), Key=to_av(key), ConsistentRead=consistent)
        item = resp.get("Item")
        return from_av(item) if item else None

    def query_page(
        self,
        table: T.Table,
        key_condition: str,
        values: Mapping[str, Any],
        *,
        index: str | None = None,
        filter_expression: str | None = None,
        names: Mapping[str, str] | None = None,
        limit: int | None = None,
        forward: bool = True,
        start_key: Mapping[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Exactly one Query request. Returns (items, LastEvaluatedKey)."""
        kwargs: dict[str, Any] = {
            "TableName": self.name(table),
            "KeyConditionExpression": key_condition,
            "ExpressionAttributeValues": to_av(values),
            "ScanIndexForward": forward,
        }
        if index:
            kwargs["IndexName"] = index
        if filter_expression:
            kwargs["FilterExpression"] = filter_expression
        if names:
            kwargs["ExpressionAttributeNames"] = dict(names)
        if limit is not None:
            kwargs["Limit"] = limit
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        resp = self.client.query(**kwargs)
        return [from_av(i) for i in resp.get("Items", [])], resp.get("LastEvaluatedKey")

    def query(
        self, table: T.Table, key_condition: str, values: Mapping[str, Any], **kw: Any
    ) -> list[dict[str, Any]]:
        """All pages. Not for the hot path — `resolve` uses `query_page`."""
        out: list[dict[str, Any]] = []
        start: dict[str, Any] | None = None
        while True:
            items, start = self.query_page(table, key_condition, values, start_key=start, **kw)
            out.extend(items)
            if not start:
                return out

    def scan_all(self, table: T.Table) -> list[dict[str, Any]]:
        """Admin/test helper (the privacy dump, the binding page's admin view). Never on a request path."""
        out: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"TableName": self.name(table)}
        while True:
            resp = self.client.scan(**kwargs)
            out.extend(from_av(i) for i in resp.get("Items", []))
            if not resp.get("LastEvaluatedKey"):
                return out
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # --- writes ------------------------------------------------------------------------------------------

    def put(
        self,
        table: T.Table,
        item: Mapping[str, Any],
        *,
        condition: str | None = None,
        names: Mapping[str, str] | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"TableName": self.name(table), "Item": to_av(item)}
        if condition:
            kwargs["ConditionExpression"] = condition
        if names:
            kwargs["ExpressionAttributeNames"] = dict(names)
        if values:
            kwargs["ExpressionAttributeValues"] = to_av(values)
        try:
            self.client.put_item(**kwargs)
        except ClientError as e:
            if _is_conditional(e):
                raise ConditionFailed(f"conditional put on {table.name} rejected") from None
            raise

    def update(
        self,
        table: T.Table,
        key: Mapping[str, Any],
        update_expression: str,
        *,
        condition: str | None = None,
        names: Mapping[str, str] | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """UpdateItem returning ALL_NEW."""
        kwargs: dict[str, Any] = {
            "TableName": self.name(table),
            "Key": to_av(key),
            "UpdateExpression": update_expression,
            "ReturnValues": "ALL_NEW",
        }
        if condition:
            kwargs["ConditionExpression"] = condition
        if names:
            kwargs["ExpressionAttributeNames"] = dict(names)
        if values:
            kwargs["ExpressionAttributeValues"] = to_av(values)
        try:
            resp = self.client.update_item(**kwargs)
        except ClientError as e:
            if _is_conditional(e):
                raise ConditionFailed(f"conditional update on {table.name} rejected") from None
            raise
        return from_av(resp.get("Attributes", {}))

    def delete(
        self,
        table: T.Table,
        key: Mapping[str, Any],
        *,
        condition: str | None = None,
        names: Mapping[str, str] | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """DeleteItem returning ALL_OLD (None if nothing was there)."""
        kwargs: dict[str, Any] = {"TableName": self.name(table), "Key": to_av(key), "ReturnValues": "ALL_OLD"}
        if condition:
            kwargs["ConditionExpression"] = condition
        if names:
            kwargs["ExpressionAttributeNames"] = dict(names)
        if values:
            kwargs["ExpressionAttributeValues"] = to_av(values)
        try:
            resp = self.client.delete_item(**kwargs)
        except ClientError as e:
            if _is_conditional(e):
                raise ConditionFailed(f"conditional delete on {table.name} rejected") from None
            raise
        old = resp.get("Attributes")
        return from_av(old) if old else None

    # --- setup (DynamoDB Local, moto, compose seed) -------------------------------------------------------

    def ensure_tables(self, *, wait_seconds: float = 30.0) -> list[str]:
        """Create any missing table from `tables.py` and enable TTL. Returns the names created.

        PITR is Terraform's job (DynamoDB Local does not implement continuous backups).
        """
        existing: set[str] = set()
        kwargs: dict[str, Any] = {}
        while True:
            resp = self.client.list_tables(**kwargs)
            existing.update(resp.get("TableNames", []))
            if not resp.get("LastEvaluatedTableName"):
                break
            kwargs["ExclusiveStartTableName"] = resp["LastEvaluatedTableName"]
        created: list[str] = []
        for t in T.TABLES:
            name = self.name(t)
            if name in existing:
                continue
            self.client.create_table(**T.create_table_kwargs(t, self.prefix))
            created.append(name)
        deadline = time.monotonic() + wait_seconds
        for name in created:
            while self.client.describe_table(TableName=name)["Table"].get("TableStatus") != "ACTIVE":
                if time.monotonic() > deadline:
                    raise TimeoutError(f"table {name} not ACTIVE after {wait_seconds}s")
                time.sleep(0.1)
            t = T.BY_NAME[name[len(self.prefix) :]]
            if t.ttl_attribute:
                self.client.update_time_to_live(
                    TableName=name,
                    TimeToLiveSpecification={"Enabled": True, "AttributeName": t.ttl_attribute},
                )
        return created
