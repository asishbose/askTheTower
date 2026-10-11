"""`append(store, record)` — the write that blocks the answer (07 §1; 02 §2 step 5, §6 "no audit, no answer").

Shape, so a crash test is honest: **append → callback → return**. The row is durable before `after_append`
runs and before the caller can build its response; `release()` packages that order for callers.

One append = one consistent GetItem (the chain head) + one TransactWriteItems:

- Put the row, conditional on `attribute_not_exists(ts_seq)` (`ts#seq` uniqueness), and
- Put the head, conditional on it still pointing at the row we chained from (or not existing yet).

A cancelled transaction means another writer appended to this line between our read and our write (a seq
collision in the wider sense): re-read the head and retry **once**. Any other failure, or a second collision,
raises `AuditWriteFailed`. Nothing here swallows it; callers must refuse.

Positioning: `ts` is the caller's clock; if it is not after the head's, the row takes the head's ts and the next
seq (the chain order is the SK order, so a row can never sort before the one it chains from).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError
from tower_consent import Store
from tower_consent import tables as T
from tower_consent.store import to_av

from tower_audit.chain import Head, head_item, read_head
from tower_audit.errors import AuditWriteFailed
from tower_audit.record import GENESIS, MAX_SEQ, AuditRecord, parse_ts_seq

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.type_defs import PutTypeDef


def _position(record: AuditRecord, head: Head | None) -> AuditRecord:
    if head is None:
        return record.positioned(record.ts, 0, GENESIS)
    last_ts, last_seq = parse_ts_seq(head.last_ts_seq)
    if record.ts > last_ts:
        ts, seq = record.ts, 0
    else:
        ts, seq = last_ts, last_seq + 1
    if seq > MAX_SEQ:
        raise AuditWriteFailed("too many audit rows at one timestamp")
    return record.positioned(ts, seq, head.last_hash)


def _transact(store: Store, row: AuditRecord, head: Head | None) -> None:
    table = store.name(T.AUDIT)
    head_put: PutTypeDef = {"TableName": table, "Item": to_av(head_item(row))}
    if head is None:
        head_put["ConditionExpression"] = "attribute_not_exists(ts_seq)"
    else:
        head_put["ConditionExpression"] = "last_ts_seq = :prev"
        head_put["ExpressionAttributeValues"] = to_av({":prev": head.last_ts_seq})
    store.client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": table,
                    "Item": to_av(row.to_item()),
                    "ConditionExpression": "attribute_not_exists(ts_seq)",
                }
            },
            {"Put": head_put},
        ]
    )


def _is_collision(e: ClientError) -> bool:
    code = e.response.get("Error", {}).get("Code", "")
    return code in ("TransactionCanceledException", "ConditionalCheckFailedException")


def append(
    store: Store,
    record: AuditRecord,
    *,
    after_append: Callable[[AuditRecord], None] | None = None,
) -> AuditRecord:
    """Write `record` as the next row of its line's chain and return the stored row.

    Raises `AuditWriteFailed` if the row is not durably written. `after_append` runs only after the write
    succeeded; an exception from it propagates unchanged (the row stays: it records a decision that was made).
    """
    row: AuditRecord | None = None
    for attempt in (1, 2):
        try:
            head = read_head(store, record.line_id)
            row = _position(record, head)
            _transact(store, row, head)
            break
        except AuditWriteFailed:
            raise
        except ClientError as e:
            if _is_collision(e) and attempt == 1:
                continue
            raise AuditWriteFailed("audit row not written") from e
        except (BotoCoreError, ValueError, KeyError) as e:
            raise AuditWriteFailed("audit row not written") from e
    if row is None:  # pragma: no cover - the loop either breaks with a row or raises
        raise AuditWriteFailed("audit row not written")
    if after_append is not None:
        after_append(row)
    return row


def release[R](
    store: Store,
    record: AuditRecord,
    respond: Callable[[AuditRecord], R],
    *,
    after_append: Callable[[AuditRecord], None] | None = None,
) -> R:
    """Audit, then build the response. If the append fails, `respond` never runs and `AuditWriteFailed`
    propagates: no audit, no answer."""
    row = append(store, record, after_append=after_append)
    return respond(row)
