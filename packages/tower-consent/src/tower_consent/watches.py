"""Watches (04 §4, 06 §7): who watches which line, with which profile, and the alert state machine's memory."""

from __future__ import annotations

from tower_consent import tables as T
from tower_consent.errors import ConditionFailed, WatchNotFound
from tower_consent.models import EscalationStep, LastState, Profile, Watch
from tower_consent.store import Store


def chain_of(contacts: list[str]) -> list[EscalationStep]:
    """Ordered contacts → `escalation[]`. `requires_ack` is derived, never chosen (04 §9.1): true on every step
    that has a successor (the next person is texted if this one doesn't reply), false on the last."""
    last = len(contacts) - 1
    return [EscalationStep(user_id=u, requires_ack=i < last) for i, u in enumerate(contacts)]


def upsert_watch(store: Store, watch: Watch) -> Watch:
    """Create or update a Watch's settings (profile, enabled, escalation, subscription ids).

    An existing `last_state` is never overwritten here — only `update_last_state` moves it — so toggling a
    watch off and on keeps the alert clock. A `last_state` on `watch` seeds a new row only.
    """
    names = {"#p": "profile", "#e": "enabled", "#esc": "escalation", "#s": "subscription_ids"}
    values: dict[str, object] = {
        ":p": watch.profile,
        ":e": watch.enabled,
        ":esc": [s.to_item() for s in watch.escalation],
        ":s": list(watch.subscription_ids),
    }
    expr = "SET #p = :p, #e = :e, #esc = :esc, #s = :s"
    if watch.last_state is not None:
        names["#ls"] = "last_state"
        values[":ls"] = watch.last_state.to_item()
        expr += ", #ls = if_not_exists(#ls, :ls)"
    item = store.update(
        T.WATCHES,
        {"line_id": watch.line_id, "watcher_user_id": watch.watcher_user_id},
        expr,
        names=names,
        values=values,
    )
    return Watch.model_validate(item)


def get_watch(store: Store, line_id: str, watcher_user_id: str) -> Watch | None:
    item = store.get(T.WATCHES, {"line_id": line_id, "watcher_user_id": watcher_user_id})
    return Watch.model_validate(item) if item else None


def list_watches_for_line(store: Store, line_id: str) -> list[Watch]:
    """Every Watch on a line (Tower's stored-state path: any fresh `last_state` will do, 02 §4)."""
    return [Watch.model_validate(i) for i in store.query(T.WATCHES, "line_id = :l", {":l": line_id})]


def list_watches_by_profile(store: Store, profile: Profile, *, enabled_only: bool = True) -> list[Watch]:
    """The poller's input (06 §1): every Watch of one profile."""
    items = store.query(T.WATCHES, "#p = :p", {":p": profile}, index="by_profile", names={"#p": "profile"})
    watches = [Watch.model_validate(i) for i in items]
    return [w for w in watches if w.enabled] if enabled_only else watches


def disable_watch(store: Store, line_id: str, watcher_user_id: str) -> bool:
    """Turn one Watch off (kept, not deleted: `last_state` and the record stay). False if there is no such Watch.

    Called by `grants.revoke` for the grantee's Watch (design rule 4: revocable any time; 04 §3). Alerts
    reconciles the line's carrier subscriptions against the remaining enabled, consented Watches (06 §4).
    """
    try:
        store.update(
            T.WATCHES,
            {"line_id": line_id, "watcher_user_id": watcher_user_id},
            "SET #e = :f",
            condition="attribute_exists(line_id)",
            names={"#e": "enabled"},
            values={":f": False},
        )
    except ConditionFailed:
        return False
    return True


def remove_contact(store: Store, line_id: str, watcher_user_id: str, contact_user_id: str) -> bool:
    """Drop one contact from a Watch's chain and re-derive `requires_ack` (04 §9.4). False if not in it.

    Called by `grants.revoke` for the line-holder's Watch when a contact's `watch` grant is revoked. Only
    `escalation` is written: `enabled`, `profile` and `last_state` stay as they are.
    """
    watch = get_watch(store, line_id, watcher_user_id)
    if watch is None or contact_user_id not in {s.user_id for s in watch.escalation}:
        return False
    remaining = [s.user_id for s in watch.escalation if s.user_id != contact_user_id]
    store.update(
        T.WATCHES,
        {"line_id": line_id, "watcher_user_id": watcher_user_id},
        "SET #esc = :esc",
        condition="attribute_exists(line_id)",
        names={"#esc": "escalation"},
        values={":esc": [s.to_item() for s in chain_of(remaining)]},
    )
    return True


def delete_watch(store: Store, line_id: str, watcher_user_id: str) -> bool:
    """Delete one Watch row (the local-only demo reset, 04 §9.2). False if there was none."""
    old = store.delete(T.WATCHES, {"line_id": line_id, "watcher_user_id": watcher_user_id})
    return old is not None


def update_last_state(store: Store, line_id: str, watcher_user_id: str, state: LastState) -> bool:
    """Write `last_state` iff it is newer than the stored one (conditional on `at`).

    Returns False when a newer (or equal) state is already stored — an out-of-order event or a concurrent poll
    lost the race, which is fine. Raises `WatchNotFound` if there is no such Watch.
    """
    key = {"line_id": line_id, "watcher_user_id": watcher_user_id}
    try:
        store.update(
            T.WATCHES,
            key,
            "SET #ls = :ls",
            condition="attribute_exists(line_id) AND (attribute_not_exists(#ls) OR #ls.#at < :at)",
            names={"#ls": "last_state", "#at": "at"},
            values={":ls": state.to_item(), ":at": state.to_item()["at"]},
        )
        return True
    except ConditionFailed:
        if store.get(T.WATCHES, key) is None:
            raise WatchNotFound("no such watch") from None
        return False
