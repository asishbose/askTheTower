"""Alerts' own small table, `AlertsState` (PK `pk`, TTL `expires_at`) — everything that is not a Watch.

`Watches.last_state` (06 §7) holds the observed facts, the unreachable clock and `last_alert_at`; it is what
Tower reads (02 §4), so only facts move it and its `at` is always the time of a complete observation. The
bookkeeping that must not bump `at` lives here instead, one item per purpose:

| pk | What | Expires |
|---|---|---|
| `sink#<token>` | sink token → `line_id` (06 §6: unguessable, exactly one line) | never (deleted on unsubscribe) |
| `line#<line_id>` | `sink_token`, `subscription_ids`, `kinds`, `subs_at`, `last_event_at`, `misses` | never |
| `evt#<digest>` | webhook dedupe: SHA-256(token, source, id) claimed with a conditional put | 7 days |
| `rl#<line_id>#<watcher>#<code>` | rate-limit claim: one alert per (line, watcher, reason) per `RATE_LIMIT` | `RATE_LIMIT` |
| `esc#<line_id>#<watcher>` | escalation in flight (06 §3): `chain_id`, `version`, step, sent_at, codes, facts, acked, ack_ignored, `remaining` (the steps after the one texted, 06 §11.3) | 2 days |
| `ack#<phone line_id>` | which escalation a reply from that phone acknowledges | 2 days |

No key or value is a phone number: line ids are HMACs, digests are re-lettered `a`–`p`, tokens are letters.

**Clocks (06 §7).** "Expires" is garbage collection, never a decision: every `expires_at` is the *wall* time of
the write (`wall`, from `AlertsService.wall`) plus the horizon, because DynamoDB deletes by the wall clock and the
mock's scenario clock may be days behind. The windows themselves compare against the service clock: the rate
limit's `sent_at <= :cut` and `Watches.last_state.last_alert_at`, an escalation's `sent_at` + `ESCALATE_NEXT`.

**Single owner of an escalation (06 §5).** Every write to an `esc#` row is conditional on the `(chain_id,
version)` the writer read (`attribute_not_exists(pk)` for a new row), and bumps `version`; deleting is conditional
the same way. `save_escalation` returns the row it wrote or None, `clear_escalation` True or False: the winner owns
the row (it alone texts, audits, parks or deletes), the loser does nothing. `chain_id` is new per chain, so a row
deleted and re-created never matches a stale reader (no ABA).
"""

from __future__ import annotations

import hashlib
import secrets
import string
import time
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from tower_consent import EscalationStep, Store
from tower_consent.errors import ConditionFailed
from tower_consent.models import format_ts
from tower_consent.tables import Key, Table, create_table_kwargs
from tower_policy import Facts, ReasonCode

ALERTS_STATE = Table(name="AlertsState", hash_key=Key("pk"), ttl_attribute="expires_at")
EVENT_TTL = timedelta(days=7)
ESC_TTL = timedelta(days=2)

_HEX_TO_LETTERS = str.maketrans("0123456789abcdef", "abcdefghijklmnop")


def digest(*parts: str) -> str:
    """SHA-256 of the parts, re-lettered a-p (no digit run can match the phone regex)."""
    h = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return h.translate(_HEX_TO_LETTERS)


def new_token() -> str:
    return "".join(secrets.choice(string.ascii_letters) for _ in range(32))


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def ensure_alerts_table(store: Store, *, wait_seconds: float = 30.0) -> bool:
    """Create `AlertsState` (and enable TTL) if missing. Returns True if created. Terraform owns it on AWS."""
    name = store.name(ALERTS_STATE)
    try:
        store.client.describe_table(TableName=name)
        return False
    except store.client.exceptions.ResourceNotFoundException:
        pass
    store.client.create_table(**create_table_kwargs(ALERTS_STATE, store.prefix))
    deadline = time.monotonic() + wait_seconds
    while store.client.describe_table(TableName=name)["Table"].get("TableStatus") != "ACTIVE":
        if time.monotonic() > deadline:
            raise TimeoutError(f"table {name} not ACTIVE after {wait_seconds}s")
        time.sleep(0.1)
    store.client.update_time_to_live(
        TableName=name, TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"}
    )
    return True


def terraform_definition(prefix: str = "") -> dict[str, Any]:
    """Same shape as `tower_consent.tables.terraform_definitions()` entries, for prompt 13."""
    return {
        "name": ALERTS_STATE.physical_name(prefix),
        "billing_mode": "PAY_PER_REQUEST",
        "hash_key": "pk",
        "range_key": None,
        "attributes": [{"name": "pk", "type": "S"}],
        "global_secondary_indexes": [],
        "ttl": {"enabled": True, "attribute_name": "expires_at"},
        "point_in_time_recovery": False,
    }


# --- sink tokens and per-line bookkeeping --------------------------------------------------------------------


class LineRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    line_id: str
    sink_token: str | None = None
    subscription_ids: list[str] = []
    kinds: list[str] | None = (
        None  # the kinds `subscription_ids` were made for; None on rows written before D7
    )
    subs_at: datetime | None = None
    last_event_at: datetime | None = None
    misses: int = 0


def get_line_row(store: Store, line_id: str) -> LineRow | None:
    item = store.get(ALERTS_STATE, {"pk": f"line#{line_id}"})
    return LineRow.model_validate(item) if item else None


def ensure_sink_token(store: Store, line_id: str) -> LineRow:
    """The line's sink token, created (with its reverse mapping) on first use."""
    row = get_line_row(store, line_id)
    if row is not None and row.sink_token:
        return row
    token = new_token()
    store.put(ALERTS_STATE, {"pk": f"sink#{token}", "line_id": line_id}, condition="attribute_not_exists(pk)")
    try:
        store.update(
            ALERTS_STATE,
            {"pk": f"line#{line_id}"},
            "SET line_id = :l, sink_token = :t",
            condition="attribute_not_exists(sink_token)",
            values={":l": line_id, ":t": token},
        )
    except ConditionFailed:  # a concurrent enable won; drop our token
        store.delete(ALERTS_STATE, {"pk": f"sink#{token}"})
    row = get_line_row(store, line_id)
    assert row is not None
    return row


def line_for_token(store: Store, token: str) -> str | None:
    if not token or len(token) > 64 or not token.isalpha():
        return None
    item = store.get(ALERTS_STATE, {"pk": f"sink#{token}"})
    return str(item["line_id"]) if item else None


def drop_sink(store: Store, line_id: str) -> None:
    row = get_line_row(store, line_id)
    if row is None:
        return
    if row.sink_token:
        store.delete(ALERTS_STATE, {"pk": f"sink#{row.sink_token}"})
    store.delete(ALERTS_STATE, {"pk": f"line#{line_id}"})


def set_subscriptions(
    store: Store, line_id: str, ids: list[str], now: datetime | None, *, kinds: list[str] | None = None
) -> None:
    store.update(
        ALERTS_STATE,
        {"pk": f"line#{line_id}"},
        "SET subscription_ids = :s, subs_at = :t, kinds = :k",
        values={":s": ids, ":t": format_ts(now) if now else None, ":k": kinds},
    )


def touch_event(store: Store, line_id: str, now: datetime) -> None:
    store.update(
        ALERTS_STATE,
        {"pk": f"line#{line_id}"},
        "SET last_event_at = :t",
        condition="attribute_exists(pk)",
        values={":t": format_ts(now)},
    )


def record_miss(store: Store, line_id: str) -> int:
    """Count one failed observation; returns the running count."""
    item = store.update(
        ALERTS_STATE,
        {"pk": f"line#{line_id}"},
        "SET line_id = :l ADD misses :one",
        values={":one": 1, ":l": line_id},
    )
    return int(item.get("misses", 0))


def reset_misses(store: Store, line_id: str) -> None:
    row = store.get(ALERTS_STATE, {"pk": f"line#{line_id}"})
    if row and row.get("misses"):
        store.update(ALERTS_STATE, {"pk": f"line#{line_id}"}, "SET misses = :z", values={":z": 0})


# --- webhook dedupe ------------------------------------------------------------------------------------------


def claim_event(store: Store, token: str, source: str, event_id: str, wall: datetime) -> bool:
    """True the first time (token, source, id) is seen; False for a duplicate (06 §6, §8). Kept `EVENT_TTL`
    past `wall` (the real time: the row holds no window)."""
    try:
        store.put(
            ALERTS_STATE,
            {"pk": f"evt#{digest(token, source, event_id)}", "expires_at": _epoch(wall + EVENT_TTL)},
            condition="attribute_not_exists(pk)",
        )
        return True
    except ConditionFailed:
        return False


# --- rate limit ----------------------------------------------------------------------------------------------


def claim_alert(
    store: Store, line_id: str, watcher: str, code: str, now: datetime, window: timedelta, *, wall: datetime
) -> bool:
    """Atomically take the (line, watcher, reason) slot for `window`. False → an alert went out within it.

    The window is `now` (service clock) against the stored `sent_at`; the row's TTL is `wall + window` and
    only tidies up. If TTL removes the row early, `last_state.last_alert_at` still holds the window."""
    try:
        store.update(
            ALERTS_STATE,
            {"pk": f"rl#{line_id}#{watcher}#{code}"},
            "SET sent_at = :now, expires_at = :exp",
            condition="attribute_not_exists(pk) OR sent_at <= :cut",
            values={
                ":now": format_ts(now),
                ":cut": format_ts(now - window),
                ":exp": _epoch(wall + window),
            },
        )
        return True
    except ConditionFailed:
        return False


# --- escalation ----------------------------------------------------------------------------------------------


class Escalation(BaseModel):
    """One alert's walk down `watch.escalation` (06 §3).

    `remaining` is the snapshot taken when the chain parked: the steps after the one just texted (06 §11.3).
    `tick` walks it, not the live chain, so a settings change cannot shift steps under an alert in flight.
    `None` on rows parked before the snapshot existed (they fall back to `watch.escalation[step + 1:]`).
    """

    model_config = ConfigDict(extra="ignore")

    line_id: str
    watcher_user_id: str
    codes: list[ReasonCode]
    facts: Facts
    alias: str | None = None
    tz: str
    step: int
    sent_at: datetime
    acked: bool = False
    ack_ignored: bool = False
    note_sent: bool = False
    remaining: list[EscalationStep] | None = None
    chain_id: str = ""  # new per chain; "" on rows parked before the single-owner rule (2026-10-11)
    version: int = 0  # bumped by every write; 0 on rows parked before the single-owner rule


def _esc_pk(line_id: str, watcher: str) -> str:
    return f"esc#{line_id}#{watcher}"


def new_chain_id() -> str:
    return new_token()


def _held_condition(held: Escalation | None) -> tuple[str, dict[str, Any]]:
    """The condition that the row is still exactly what `held` read (absent, when nothing was read)."""
    if held is None:
        return "attribute_not_exists(pk)", {}
    if held.version == 0:  # parked before rows carried a version: whoever writes first takes it
        return "attribute_exists(pk) AND attribute_not_exists(version)", {}
    return "chain_id = :c AND version = :v", {":c": held.chain_id, ":v": held.version}


def save_escalation(
    store: Store, esc: Escalation, *, wall: datetime, held: Escalation | None = None
) -> Escalation | None:
    """Write `esc` if the row is still `held` (or absent when `held` is None). Returns the row written, with
    `version` = held's + 1 (a new chain id when `esc` has none), or None when another writer got there first.

    `ESCALATE_NEXT` runs from `esc.sent_at` (service clock); the row's TTL is `wall + ESC_TTL`, real time."""
    written = esc.model_copy(
        update={
            "version": (held.version if held is not None else 0) + 1,
            "chain_id": esc.chain_id or new_chain_id(),
        }
    )
    item = written.model_dump(mode="json")
    item.update(pk=_esc_pk(esc.line_id, esc.watcher_user_id), expires_at=_epoch(wall + ESC_TTL))
    condition, values = _held_condition(held)
    try:
        store.put(ALERTS_STATE, item, condition=condition, values=values)
    except ConditionFailed:
        return None
    return written


def get_escalation(store: Store, line_id: str, watcher: str) -> Escalation | None:
    item = store.get(ALERTS_STATE, {"pk": _esc_pk(line_id, watcher)})
    return Escalation.model_validate(item) if item else None


def clear_escalation(store: Store, held: Escalation) -> bool:
    """Delete the row if it is still `held`. True → deleted by this call; False → another writer moved it."""
    condition, values = _held_condition(held)
    try:
        store.delete(
            ALERTS_STATE,
            {"pk": _esc_pk(held.line_id, held.watcher_user_id)},
            condition=condition,
            values=values,
        )
    except ConditionFailed:
        return False
    return True


def list_escalations(store: Store) -> list[Escalation]:
    """Every escalation in flight (the poller's tick). A filtered Scan over a table of a few items per line."""
    out: list[Escalation] = []
    kwargs: dict[str, Any] = {
        "TableName": store.name(ALERTS_STATE),
        "FilterExpression": "begins_with(pk, :p)",
        "ExpressionAttributeValues": {":p": {"S": "esc#"}},
    }
    from tower_consent.store import from_av

    while True:
        resp = store.client.scan(**kwargs)
        out.extend(Escalation.model_validate(from_av(i)) for i in resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            return sorted(out, key=lambda e: (e.line_id, e.watcher_user_id))
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def remember_ack_route(store: Store, phone_line_id: str, line_id: str, watcher: str, wall: datetime) -> None:
    """Route a reply from `phone_line_id` to this escalation; kept `ESC_TTL` past `wall` (real time)."""
    store.put(
        ALERTS_STATE,
        {
            "pk": f"ack#{phone_line_id}",
            "line_id": line_id,
            "watcher_user_id": watcher,
            "expires_at": _epoch(wall + ESC_TTL),
        },
    )


def ack_route(store: Store, phone_line_id: str) -> tuple[str, str] | None:
    item = store.get(ALERTS_STATE, {"pk": f"ack#{phone_line_id}"})
    return (str(item["line_id"]), str(item["watcher_user_id"])) if item else None
