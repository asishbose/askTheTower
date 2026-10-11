"""D7 (code-vs-docs.md): revoking a `watch` grant disables the grantee's Watch on that line, and only that one.

Design rule 4 (revocable any time), 04 §3. Disabled, not deleted: the record and `last_state` stay.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from tower_consent import (
    LastState,
    Store,
    bind_line,
    disable_watch,
    get_watch,
    grant,
    revoke,
    upsert_watch,
)
from tower_consent.crypto import LineIdHasher, MsisdnCipher
from tower_consent.models import Watch

pytestmark = pytest.mark.integration


@pytest.fixture
def lines(store: Store, hasher: LineIdHasher, cipher: MsisdnCipher, now: datetime) -> dict[str, str]:
    mom = bind_line(store, hasher, cipher, "mom", "+15555550123", "auth_code", now=now).line_id
    asish = bind_line(store, hasher, cipher, "asish", "+15555550125", "auth_code", now=now).line_id
    return {"mom": mom, "asish": asish}


def test_revoke_watch_disables_only_the_grantees_watch_on_that_line(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    mom, asish = lines["mom"], lines["asish"]
    grant(store, mom, "asish", "watch", "mom", granted_by="mom", now=now)
    grant(store, mom, "priya", "watch", "mom", granted_by="mom", now=now)
    seen = LastState(cf_status="none", at=now)
    upsert_watch(store, Watch(line_id=mom, watcher_user_id="asish", profile="care", last_state=seen))
    upsert_watch(store, Watch(line_id=mom, watcher_user_id="priya", profile="care"))
    upsert_watch(store, Watch(line_id=mom, watcher_user_id="mom", profile="self"))
    upsert_watch(store, Watch(line_id=asish, watcher_user_id="asish", profile="self"))

    revoke(store, mom, "asish", "watch", revoked_by="mom", now=now + timedelta(seconds=1))

    w = get_watch(store, mom, "asish")
    assert w is not None and not w.enabled  # disabled, not deleted
    assert w.last_state == seen  # the record stays
    for line, user in ((mom, "priya"), (mom, "mom"), (asish, "asish")):
        other = get_watch(store, line, user)
        assert other is not None and other.enabled, (line, user)


def test_revoke_reachability_leaves_the_watch_alone(
    store: Store, lines: dict[str, str], now: datetime
) -> None:
    """A `reachability` grant never covered alerts (04 §3), so revoking it changes no Watch."""
    mom = lines["mom"]
    grant(store, mom, "asish", "watch", "mom", granted_by="mom", now=now)
    grant(store, mom, "asish", "reachability", "mum", granted_by="mom", now=now)
    upsert_watch(store, Watch(line_id=mom, watcher_user_id="asish", profile="care"))
    revoke(store, mom, "asish", "reachability", revoked_by="mom", now=now)
    w = get_watch(store, mom, "asish")
    assert w is not None and w.enabled


def test_revoke_without_a_watch_is_fine(store: Store, lines: dict[str, str], now: datetime) -> None:
    mom = lines["mom"]
    grant(store, mom, "asish", "watch", "mom", granted_by="mom", now=now)
    revoke(store, mom, "asish", "watch", revoked_by="mom", now=now)
    assert get_watch(store, mom, "asish") is None  # no Watch is created by a revoke
    assert disable_watch(store, mom, "asish") is False
