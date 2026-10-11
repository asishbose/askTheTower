"""Content (07 §3, §6): every stored field scanned — nothing digit-shaped like a number, `message_ref` only
known template ids, no free-text fields."""

from __future__ import annotations

import re
from typing import Any

import pytest
from tower_audit import TEMPLATE_IDS, AuditRecord, append, trim
from tower_audit.chain import HEAD_SK
from tower_consent import Store
from tower_consent import tables as T
from tower_policy import ReasonCode

from tests.privacy.patterns import HEALTH_WORDS, phone_hits

from .conftest import NOW, RecordFactory

pytestmark = pytest.mark.integration

ROW_ATTRS = {
    "line_id",
    "ts_seq",
    "actor_user_id",
    "tool",
    "trigger",
    "source",
    "outcome",
    "reason_codes",
    "message_ref",
    "policy_version",
    "prev_hash",
    "ttl",
}
HEAD_ATTRS = {"line_id", "ts_seq", "last_ts_seq", "last_hash", "ttl"}
VOCAB = {
    "tool": {"line_is_ok", "is_reachable", "watch_line", "alert"},
    "trigger": {"voice", "poll", "event", "binding"},
    "source": {"carrier", "watch"},
    "outcome": {"ok", "changed", "refused", "suppressed"},
}
LINE_ID = re.compile(r"ln_[a-p]{64}")
TS_SEQ = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z#\d{4}")
LETTERS = re.compile(r"[a-p]{64}")
ACTOR = re.compile(r"(system:alerts|user-[a-z]+)")


def every_kind_of_row(make_record: RecordFactory) -> list[AuditRecord]:
    alerts = {"actor_user_id": "system:alerts", "tool": "alert", "source": None}
    return [
        make_record(0),
        make_record(1, source="watch"),
        make_record(2, outcome="changed", reason_codes=["SIM_SWAPPED_RECENT", "CALL_FORWARDING_SET"]),
        make_record(3, tool="is_reachable", outcome="changed", reason_codes=["UNREACHABLE"]),
        make_record(4, outcome="refused", reason_codes=["NO_CONSENT"], source=None),
        make_record(5, outcome="refused", reason_codes=["STALE_DATA"], source="watch"),
        make_record(6, tool="watch_line", trigger="voice", source=None, actor_user_id="user-mom"),
        make_record(7, tool="watch_line", trigger="binding", source=None, actor_user_id="user-mom"),
        make_record(
            8,
            **alerts,
            trigger="event",
            outcome="changed",
            reason_codes=["SIM_SWAPPED_RECENT"],
            message_ref="SIM_SWAPPED_RECENT.sms",
        ),
        make_record(
            9,
            **alerts,
            trigger="poll",
            outcome="changed",
            reason_codes=["UNREACHABLE"],
            message_ref="UNREACHABLE.sms",
        ),
        make_record(10, **alerts, trigger="event", outcome="suppressed", reason_codes=["SUPPRESSED_REVOKED"]),
        make_record(11, **alerts, trigger="poll", outcome="refused", reason_codes=["ALERT_FAILED"]),
        make_record(
            12, **alerts, trigger="event", outcome="refused", reason_codes=["ACK_IGNORED_SWAPPED_LINE"]
        ),
        make_record(13, **alerts, trigger="poll", outcome="refused", reason_codes=["CARRIER_ERROR"]),
    ]


def check_value(attr: str, value: Any) -> None:
    if attr in VOCAB:
        assert value in VOCAB[attr], (attr, value)
    elif attr == "line_id":
        assert LINE_ID.fullmatch(value)
    elif attr in ("ts_seq", "last_ts_seq"):
        assert TS_SEQ.fullmatch(value)
    elif attr in ("policy_version", "last_hash"):
        assert LETTERS.fullmatch(value)
    elif attr == "prev_hash":
        assert value == "genesis" or LETTERS.fullmatch(value) or value.startswith("trimmed|")
    elif attr == "actor_user_id":
        assert ACTOR.fullmatch(value)
    elif attr == "reason_codes":
        assert value and all(c in ReasonCode.__members__ for c in value)
    elif attr == "message_ref":
        assert value in TEMPLATE_IDS
    elif attr == "ttl":
        assert isinstance(value, int)
    else:  # pragma: no cover
        pytest.fail(f"unexpected attribute {attr}")


def test_every_stored_field(store: Store, make_record: RecordFactory, signer: Any) -> None:
    rows = [append(store, r) for r in every_kind_of_row(make_record)]
    trim(store, rows[0].line_id, rows[3].ts, signer=signer, now=NOW)  # a marker is stored content too
    items = store.scan_all(T.AUDIT)
    assert len(items) == len(rows) + 1
    for item in items:
        attrs = HEAD_ATTRS if item["ts_seq"] == HEAD_SK else ROW_ATTRS
        assert set(item) <= attrs, set(item) - attrs  # no other (free-text) field exists
        for attr, value in item.items():
            if attr == "ttl":
                continue  # epoch seconds: the one number allowed (10 digits)
            text = str(value)
            assert not phone_hits(text), (attr, text)
            assert not HEALTH_WORDS.search(text), (attr, text)
            if item["ts_seq"] != HEAD_SK:
                check_value(attr, value)
        if "message_ref" in item:
            assert item["message_ref"] in TEMPLATE_IDS


def test_message_ref_rejects_a_body(make_record: RecordFactory) -> None:
    with pytest.raises(ValueError):
        make_record(message_ref="SIM moved to another device at 9:14 on Tue. Not you? Call your carrier now.")


def test_number_cannot_be_smuggled_as_actor(make_record: RecordFactory) -> None:
    for bad in ("+15555550123", "15555550123", "tel:15555550123"):
        with pytest.raises(ValueError):
            make_record(actor_user_id=bad)
