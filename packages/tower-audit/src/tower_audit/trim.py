"""`trim(store, line_id, before, signer=..., now=...)` — re-anchor a line's chain (07 §5).

TTL (`ttl` = ts + 90 days) does the deleting. Trim only writes the marker: the oldest row at or after `before`
gets `prev_hash = trimmed|<now>|<its original prev_hash>|<HMAC>`, conditional on its prev_hash being unchanged.
`verify` then starts its walk at that row and reports `trimmed=True`; the rows before it may disappear in any
order without breaking verification.

Run it ahead of TTL — the nightly job calls `trim(..., before=now - (RETENTION - 1 day))` per line — so the chain
is re-anchored before DynamoDB starts deleting (deletion is lazy and unordered).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict
from tower_consent import Store
from tower_consent import tables as T
from tower_consent.errors import ConditionFailed

from tower_audit.chain import MarkerSigner, make_marker, parse_marker
from tower_audit.reader import query_rows
from tower_audit.record import RETENTION

TRIM_LEAD = timedelta(days=1)


class TrimResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_ts: datetime  # the new oldest row
    rows_before: int  # rows older than the anchor, left for TTL


def trim_horizon(now: datetime) -> datetime:
    """The `before` the nightly job uses: one day ahead of TTL."""
    return now - (RETENTION - TRIM_LEAD)


def trim(
    store: Store, line_id: str, before: datetime, *, signer: MarkerSigner, now: datetime
) -> TrimResult | None:
    """Re-anchor at the oldest row with ts ≥ `before`. None if there is nothing older to trim (or nothing
    newer to anchor on — then the whole chain expires and the next append starts a new one)."""
    rows = query_rows(store, line_id)
    idx = next((i for i, r in enumerate(rows) if r.ts >= before), None)
    if idx is None or idx == 0:
        return None
    anchor = rows[idx]
    original = anchor.prev_hash or ""
    if parse_marker(original) is not None:
        return TrimResult(anchor_ts=anchor.ts, rows_before=idx)  # already anchored here
    marker = make_marker(signer, line_id, anchor.ts_seq, now, original)
    try:
        store.update(
            T.AUDIT,
            {"line_id": line_id, "ts_seq": anchor.ts_seq},
            "SET prev_hash = :m",
            condition="prev_hash = :orig",
            values={":m": marker.text(), ":orig": original},
        )
    except ConditionFailed:
        raise RuntimeError("audit row changed while trimming; re-run trim") from None
    return TrimResult(anchor_ts=anchor.ts, rows_before=idx)
