"""Dump every table after a full exercise; grep `\\+?\\d{10,15}` → nothing, `msisdn_enc` ciphertext included.

(Every `store` fixture also runs the same sweep on teardown, so the whole suite's tables are covered.)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import pytest
from tower_consent import (
    LastState,
    Store,
    Watch,
    bind_line,
    consume_bind_token,
    create_bind_token,
    ensure_user,
    grant,
    resolve,
    revoke,
    set_alert_phone,
    tables,
    update_last_state,
    upsert_watch,
)
from tower_consent.crypto import LineIdHasher, MsisdnCipher

from tests.privacy.patterns import E164_STRICT, phone_hits

pytestmark = pytest.mark.integration

NUMBERS = [f"+1555555{i:04d}" for i in range(40)] + ["+447700900123", "+4915123456789"]


def test_tables_hold_no_phone_numbers(
    store: Store,
    hasher: LineIdHasher,
    cipher: MsisdnCipher,
    now: datetime,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    lines = []
    for i, e164 in enumerate(NUMBERS):
        user = f"user-{chr(97 + i % 26)}{i}"
        ensure_user(store, user, now=now, alexa_link_id=f"amzn-{user}")
        set_alert_phone(store, cipher, user, e164)
        tok = create_bind_token(store, user, now=now)
        consume_bind_token(store, tok.token, user, now=now)
        lines.append((user, bind_line(store, hasher, cipher, user, e164, "auth_code", "mock", now=now)))
    create_bind_token(store, "pending", now=now)  # left in the table on purpose
    owner, line = lines[0]
    grantee = lines[1][0]
    grant(store, line.line_id, grantee, "watch", "mom", granted_by=owner, now=now)
    grant(store, lines[2][1].line_id, grantee, "reachability", "dad", granted_by=lines[2][0], now=now)
    revoke(store, lines[2][1].line_id, grantee, "reachability", revoked_by=lines[2][0], now=now)
    upsert_watch(store, Watch(line_id=line.line_id, watcher_user_id=grantee, profile="care"))
    update_last_state(
        store,
        line.line_id,
        grantee,
        LastState(at=now + timedelta(minutes=5), reachable=False, unreachable_since=now, sim_change_at=now),
    )
    assert resolve(store, grantee, "mom").view.grant == "watch"

    dump: dict[str, list[dict[str, object]]] = {t.name: store.scan_all(t) for t in tables.TABLES}
    assert sum(len(v) for v in dump.values()) > 2 * len(NUMBERS)
    text = json.dumps(dump, default=str)
    assert phone_hits(text) == [] or _only_ttl_epochs(dump)
    for e164 in NUMBERS:
        assert e164 not in text and e164.lstrip("+") not in text
    for item in dump["Lines"]:
        assert not E164_STRICT.search(str(item["msisdn_enc"]))
    for item in dump["Users"]:
        assert not E164_STRICT.search(str(item["alert_phone_enc"]))

    # Logs: tower-consent itself logs nothing; across *all* loggers at DEBUG (botocore dumps request and
    # response bodies) none of the numbers appears — only HMACs and ciphertexts ever reach DynamoDB.
    assert [r for r in caplog.records if r.name.startswith("tower_consent")] == []
    out = capsys.readouterr()
    logged = "\n".join(r.getMessage() for r in caplog.records) + out.out + out.err
    for e164 in NUMBERS:
        assert e164 not in logged and e164.lstrip("+") not in logged


def _only_ttl_epochs(dump: dict[str, list[dict[str, object]]]) -> bool:
    """The JSON dump may contain epoch-second TTLs (10 digits). They must be the only matches."""
    without = {
        name: [{k: v for k, v in item.items() if k != tables.BY_NAME[name].ttl_attribute} for item in items]
        for name, items in dump.items()
    }
    return phone_hits(json.dumps(without, default=str)) == []
