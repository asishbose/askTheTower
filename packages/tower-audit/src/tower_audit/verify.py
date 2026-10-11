"""`verify(store, line_id, signer)` — walk one line's chain (07 §2: per line, so one resident's log is verified
without reading anyone else's).

Where the walk starts: the newest *anchor* — a row whose `prev_hash` is a validly signed trim marker, or a
`"genesis"` row that is either the oldest row present or one before which every row had already expired (its
predecessors' `ttl` ≤ its own ts, i.e. the chain lapsed and restarted). Rows older than the anchor are trimmed
history awaiting TTL deletion and are not walked. `trimmed` is True when the walk starts at a trim marker or
older rows were skipped.

Links: row i's `prev_hash` must equal `row_hash(row i-1)`; the head item's `last_hash` must equal
`row_hash(newest row)` (so a tampered or deleted newest row is caught too).

Naming the bad row: if the first broken link is i-1 → i and the next link (i → i+1, or i → head) is also
broken, row i itself was altered (its `prev_hash` changed, so its own hash did too); otherwise row i-1's
content was altered (or the row after it deleted). `first_bad_ts` is that row's ts.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, ValidationError
from tower_consent import Store

from tower_audit.chain import MarkerSigner, marker_valid, parse_marker, read_head, row_hash
from tower_audit.reader import query_items
from tower_audit.record import GENESIS, AuditRecord, parse_ts_seq


class VerifyResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    rows: int  # rows walked (from the anchor to the newest)
    trimmed: bool
    first_bad_ts: datetime | None = None


def _is_anchor(rows: list[AuditRecord], i: int, signer: MarkerSigner) -> bool:
    row = rows[i]
    if row.prev_hash == GENESIS:
        return i == 0 or all(r.ttl <= int(row.ts.timestamp()) for r in rows[:i])
    return parse_marker(row.prev_hash or "") is not None and marker_valid(signer, row)


def verify(store: Store, line_id: str, signer: MarkerSigner) -> VerifyResult:
    rows: list[AuditRecord] = []
    for item in query_items(store, line_id):
        try:
            rows.append(AuditRecord.from_item(item))
        except (ValidationError, ValueError, KeyError):
            # A row that no longer parses (an enum or id edited into something else) is itself the bad row.
            bad_ts, _ = parse_ts_seq(str(item.get("ts_seq", "")))
            return VerifyResult(ok=False, rows=len(rows), trimmed=False, first_bad_ts=bad_ts)
    head = read_head(store, line_id)
    if not rows:
        # A head with no rows: the tail was deleted (unless the head itself has expired with its last row).
        return VerifyResult(ok=head is None, rows=0, trimmed=False)

    start = 0
    for i in range(len(rows) - 1, -1, -1):
        if _is_anchor(rows, i, signer):
            start = i
            break
    walked = rows[start:]
    trimmed = start > 0 or parse_marker(walked[0].prev_hash or "") is not None

    if not _is_anchor(rows, start, signer):
        # The oldest row is neither genesis nor a valid trim marker: forged marker, edited prev_hash, or
        # rows deleted from the front without a trim.
        return VerifyResult(ok=False, rows=len(walked), trimmed=trimmed, first_bad_ts=walked[0].ts)

    hashes = [row_hash(r) for r in walked]
    # links[k] is True when walked[k]'s predecessor link holds; links[len] is the head link.
    links = [True] + [walked[k].prev_hash == hashes[k - 1] for k in range(1, len(walked))]
    links.append(head is not None and head.last_hash == hashes[-1] and head.last_ts_seq == walked[-1].ts_seq)

    for k in range(1, len(links)):
        if links[k]:
            continue
        if k == len(walked):  # only the head link is broken: the newest row (or the head) was altered
            bad = walked[-1]
        elif not links[k + 1]:
            bad = walked[k]
        else:
            bad = walked[k - 1]
        return VerifyResult(ok=False, rows=len(walked), trimmed=trimmed, first_bad_ts=bad.ts)
    return VerifyResult(ok=True, rows=len(walked), trimmed=trimmed)
