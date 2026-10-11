"""Seed the consent store with the demo's people and lines (08 §2 `demo.yaml`): Asish owns his line, Mom owns
hers and has granted Asish `watch` under the alias "mom". Used by `make showcase-tower`, the latency harness
and the tests. Numbers come in as arguments (from the scenario file) and leave only as `line_id`s and
ciphertext.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tower_consent import (
    LastState,
    LineIdHasher,
    MsisdnCipher,
    Store,
    Watch,
    bind_line,
    ensure_user,
    grant,
    update_last_state,
    upsert_watch,
)

ASISH_USER = "user-asish"
MOM_USER = "user-mom"
MOM_ALIAS = "mom"


@dataclass(frozen=True)
class DemoSeed:
    asish_user: str
    mom_user: str
    asish_line: str
    mom_line: str


def seed_demo(
    store: Store,
    hasher: LineIdHasher,
    cipher: MsisdnCipher,
    *,
    asish_e164: str,
    mom_e164: str,
    now: datetime,
) -> DemoSeed:
    for user in (ASISH_USER, MOM_USER):
        ensure_user(store, user, now=now)
    asish = bind_line(store, hasher, cipher, ASISH_USER, asish_e164, "auth_code", now=now)
    mom = bind_line(store, hasher, cipher, MOM_USER, mom_e164, "auth_code", now=now)
    grant(store, mom.line_id, ASISH_USER, "watch", MOM_ALIAS, granted_by=MOM_USER, now=now)
    return DemoSeed(ASISH_USER, MOM_USER, asish.line_id, mom.line_id)


def seed_watch(
    store: Store, line_id: str, watcher: str, state: LastState | None, *, profile: str = "self"
) -> None:
    """A Watch with a `last_state` (what Alerts would have written) — the stored-state path of 02 §4."""
    upsert_watch(store, Watch(line_id=line_id, watcher_user_id=watcher, profile=profile))  # type: ignore[arg-type]
    if state is not None:
        update_last_state(store, line_id, watcher, state)
