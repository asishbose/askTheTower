"""Demo reset, `POST /internal/reset {user_ids}` (06 §3.2; U3 2026-10-11).

The AWS counterpart of the Watches/`AlertsState` part of `make seed`, for the demo UI's Reset on ENV=aws. For every
Line owned by one of the given users: delete every `Watches` row on it, and every `AlertsState` row keyed on it
(`line#<line_id>`, the `sink#<token>` it names, `esc#<line_id>#…`, `rl#<line_id>#…`, `ack#<line_id>`). Webhook
dedupe rows (`evt#…`) are left to their TTL. `Users`, `Lines`, `Grants`, `Audit` and `SentSms` are never touched,
and no audit row is written: a demo reset, not a resident's choice. Nothing is unsubscribed at the carrier; the
caller reloads the mock scenario first, which drops the mock's subscriptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tower_consent import Store, list_lines, list_watches_for_line
from tower_consent import tables as T
from tower_consent.store import from_av

from alerts.state import ALERTS_STATE, get_line_row

# Prefixes that carry the line id followed by '#'; line ids are fixed-shape HMACs, so the prefix is exact.
_PREFIXED = ("esc#", "rl#")


@dataclass(frozen=True)
class ResetResult:
    lines: int
    watches_deleted: int
    alerts_state_deleted: int


def _prefixed_keys(store: Store) -> list[str]:
    """Every `esc#…` and `rl#…` key: one filtered Scan of a table holding a few items per line (admin only)."""
    kwargs: dict[str, Any] = {
        "TableName": store.name(ALERTS_STATE),
        "FilterExpression": "begins_with(pk, :e) OR begins_with(pk, :r)",
        "ExpressionAttributeValues": {":e": {"S": _PREFIXED[0]}, ":r": {"S": _PREFIXED[1]}},
        "ProjectionExpression": "pk",
    }
    keys: list[str] = []
    while True:
        resp = store.client.scan(**kwargs)
        keys.extend(str(from_av(i)["pk"]) for i in resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            return keys
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _state_keys(store: Store, line_id: str, prefixed: list[str]) -> list[str]:
    keys = [f"line#{line_id}", f"ack#{line_id}"]
    row = get_line_row(store, line_id)
    if row is not None and row.sink_token:
        keys.append(f"sink#{row.sink_token}")
    keys.extend(k for k in prefixed if any(k.startswith(f"{p}{line_id}#") for p in _PREFIXED))
    return keys


def reset_users(store: Store, user_ids: list[str]) -> ResetResult:
    """Delete the Watches and line-keyed `AlertsState` rows of every line the users own; counts only."""
    line_ids = sorted({ln.line_id for u in user_ids for ln in list_lines(store, u)})
    prefixed = _prefixed_keys(store) if line_ids else []
    watches = state = 0
    for line_id in line_ids:
        for w in list_watches_for_line(store, line_id):
            if store.delete(T.WATCHES, {"line_id": line_id, "watcher_user_id": w.watcher_user_id}):
                watches += 1
        for key in _state_keys(store, line_id, prefixed):
            if store.delete(ALERTS_STATE, {"pk": key}):
                state += 1
    return ResetResult(lines=len(line_ids), watches_deleted=watches, alerts_state_deleted=state)
