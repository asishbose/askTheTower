"""AuditRecord: exactly 07 §2, canonical JSON stable and digit-free where it matters."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from tower_audit import RETENTION, TEMPLATE_IDS, AuditRecord, audit_outcome
from tower_audit.record import encode_digest, parse_ts_seq
from tower_consent import LocalLineIdHasher
from tower_policy import ReasonCode, policy_version

pytestmark = pytest.mark.unit

LINE = LocalLineIdHasher(b"k" * 32).line_id("+15555550123")
NOW = datetime(2026, 10, 6, 14, 30, tzinfo=UTC)
BASE = {
    "line_id": LINE,
    "ts": NOW,
    "actor_user_id": "user-asish",
    "tool": "line_is_ok",
    "trigger": "voice",
    "source": "watch",
    "outcome": "changed",
    "reason_codes": ["SIM_SWAPPED_RECENT", "CALL_FORWARDING_SET"],
    "message_ref": "SIM_SWAPPED_RECENT.sms",
    "policy_version": policy_version(),
    "prev_hash": "genesis",
}


def test_fields_are_exactly_07_section_2() -> None:
    assert set(AuditRecord.model_fields) == {
        "line_id",
        "ts",
        "seq",  # ts#seq is the SK
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


def test_canonical_form() -> None:
    rec = AuditRecord.model_validate(BASE)
    text = rec.canonical()
    obj = json.loads(text)
    assert list(obj) == sorted(obj)
    assert " " not in text and "\n" not in text
    assert obj["ts"] == "2026-10-06T14:30:00.000000Z"
    assert obj["ttl"] == int((NOW + RETENTION).timestamp())
    assert obj["policy_version"] == encode_digest(policy_version())
    assert all(c in "abcdefghijklmnop" for c in obj["policy_version"])
    assert not any(isinstance(v, float) for v in obj.values())


@settings(max_examples=50, deadline=None, derandomize=True)
@given(st.randoms(use_true_random=False))
def test_canonical_stable_under_field_reordering(rnd: random.Random) -> None:
    keys = list(BASE)
    rnd.shuffle(keys)
    shuffled = {k: BASE[k] for k in keys}
    assert AuditRecord.model_validate(shuffled).canonical() == AuditRecord.model_validate(BASE).canonical()


def test_canonical_independent_of_input_timezone() -> None:
    other = {**BASE, "ts": NOW.astimezone(timezone(timedelta(hours=-7)))}
    assert AuditRecord.model_validate(other).canonical() == AuditRecord.model_validate(BASE).canonical()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("line_id", "+15555550123"),
        ("line_id", "ln_0123"),
        ("actor_user_id", "+15555550123"),
        ("actor_user_id", "user 15555550123"),
        ("actor_user_id", "Mom says hi"),
        ("message_ref", "SIM moved to another device at 9:14. Not you? Call your carrier now."),
        ("message_ref", "SUPPRESSED_REVOKED.sms"),
        ("reason_codes", []),
        ("reason_codes", ["FELL_DOWN"]),
        ("outcome", "refuse"),
        ("tool", "sim_swap"),
        ("policy_version", "v1"),
        ("prev_hash", "0" * 64),
        ("ts", datetime(2026, 10, 6, 14, 30)),
    ],
)
def test_rejects_free_text_numbers_and_unknown_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AuditRecord.model_validate({**BASE, field: value})


def test_no_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AuditRecord.model_validate({**BASE, "note": "anything"})
    with pytest.raises(ValidationError):
        AuditRecord.model_validate({**BASE, "latest_sim_change": NOW})


def test_floats_never_reach_canonical() -> None:
    from tower_audit.record import _no_floats

    _no_floats({"x": [1, "a", None]})
    with pytest.raises(TypeError):
        _no_floats({"x": [1, 2.5]})


def test_template_ids_are_sms_templates_only() -> None:
    assert "UNREACHABLE.sms" in TEMPLATE_IDS
    assert not any(t.startswith(("SUPPRESSED_REVOKED", "ALERT_FAILED", "ACK_IGNORED")) for t in TEMPLATE_IDS)
    assert all(t.endswith(".sms") for t in TEMPLATE_IDS)


def test_item_round_trip_and_ts_seq() -> None:
    rec = AuditRecord.model_validate({**BASE, "seq": 3})
    item = rec.to_item()
    assert item["ts_seq"] == "2026-10-06T14:30:00.000000Z#0003"
    assert parse_ts_seq(item["ts_seq"]) == (NOW, 3)
    assert "ts" not in item and "seq" not in item
    assert AuditRecord.from_item(item) == rec


def test_positioned_recomputes_ttl() -> None:
    rec = AuditRecord.model_validate(BASE)
    later = rec.positioned(NOW + timedelta(days=1), 2, "a" * 64)
    assert later.ttl == rec.ttl + 86400 and later.seq == 2 and later.prev_hash == "a" * 64


def test_unpositioned_record_cannot_be_stored() -> None:
    with pytest.raises(ValueError):
        AuditRecord.model_validate({**BASE, "prev_hash": None}).to_item()


def test_audit_outcome_mapping() -> None:
    assert [audit_outcome(k) for k in ("ok", "changed", "refuse")] == ["ok", "changed", "refused"]


def test_reason_codes_are_policy_codes() -> None:
    rec = AuditRecord.model_validate(BASE)
    assert all(isinstance(c, ReasonCode) for c in rec.reason_codes)
