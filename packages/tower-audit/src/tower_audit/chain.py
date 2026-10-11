"""The per-line hash chain: head lookup, row hashes, and the signed `trimmed_at` marker.

Chain shape (07 §2, §5):

- Row *n*'s `prev_hash` is `row_hash(row n-1)` = SHA-256 of row n-1's canonical JSON, re-lettered a-p.
  A line's first row carries `"genesis"`.
- A **head item** per line (same table, SK `"~head"`, which sorts after every `ts#seq`) holds `last_ts_seq` and
  `last_hash` (= `row_hash` of the newest row). `append` moves it in the same transaction as the row put, with
  a condition on its previous value, so two concurrent appends cannot fork the chain, and `verify` can tell a
  tampered or deleted *newest* row from an intact one. It carries the newest row's `ttl` and expires with it.
- `trim` replaces the new oldest row's `prev_hash` with
  `trimmed|<trimmed_at>|<original prev_hash>|<HMAC signature>`. The row's hash is always computed over the
  *original* prev_hash, so the next row's link still holds; the signature (over line_id, ts_seq, trimmed_at and
  the original prev_hash, domain-separated) proves the marker was written by a holder of the HMAC key.

The head is read fresh for every append (and again on the one retry): nothing is cached across appends.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from tower_consent import Store
from tower_consent import tables as T

from tower_audit.record import GENESIS, MARKER_RE, AuditRecord, encode_digest

if TYPE_CHECKING:
    from mypy_boto3_kms import KMSClient

HEAD_SK: Final = "~head"
_DOMAIN: Final = b"tower-audit/trimmed_at/v1|"


def row_hash(record: AuditRecord) -> str:
    """SHA-256 of the row's canonical JSON (with a trim marker's original prev_hash restored), re-lettered."""
    marker = parse_marker(record.prev_hash) if record.prev_hash else None
    prev = marker.original_prev_hash if marker else record.prev_hash
    digest = hashlib.sha256(record.canonical(prev_hash=prev).encode("ascii")).hexdigest()
    return encode_digest(digest)


# --- head -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Head:
    last_ts_seq: str
    last_hash: str
    ttl: int


def read_head(store: Store, line_id: str) -> Head | None:
    """One consistent GetItem. Not cached."""
    item = store.get(T.AUDIT, {"line_id": line_id, "ts_seq": HEAD_SK}, consistent=True)
    if not item:
        return None
    return Head(str(item["last_ts_seq"]), str(item["last_hash"]), int(item.get("ttl", 0)))


def head_item(row: AuditRecord) -> dict[str, Any]:
    return {
        "line_id": row.line_id,
        "ts_seq": HEAD_SK,
        "last_ts_seq": row.ts_seq,
        "last_hash": row_hash(row),
        "ttl": row.ttl,
    }


# --- trim marker ----------------------------------------------------------------------------------------------


@runtime_checkable
class MarkerSigner(Protocol):
    """HMAC over the marker fields. Output is 64 letters a-p (a re-lettered HMAC-SHA256)."""

    def sign(self, message: bytes) -> str: ...

    def verify(self, message: bytes, signature: str) -> bool: ...


class HmacMarkerSigner:
    """Local HMAC-SHA256 (`TOWER_ENV=local`, tests). Domain-separated from the line_id HMAC on the same key."""

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("HMAC key must be at least 32 bytes")
        self._key = key

    def __repr__(self) -> str:
        return "HmacMarkerSigner(key=<redacted>)"

    def sign(self, message: bytes) -> str:
        return encode_digest(hmac.new(self._key, _DOMAIN + message, hashlib.sha256).hexdigest())

    def verify(self, message: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(message), signature)


class KmsMarkerSigner:
    """KMS `GenerateMac` on the HMAC_256 key that also derives `line_id` (the key never leaves KMS)."""

    def __init__(self, kms: KMSClient, key_id: str) -> None:
        self._kms = kms
        self._key_id = key_id

    def __repr__(self) -> str:
        return "KmsMarkerSigner(key_id=<configured>)"

    def sign(self, message: bytes) -> str:
        resp = self._kms.generate_mac(
            KeyId=self._key_id, Message=_DOMAIN + message, MacAlgorithm="HMAC_SHA_256"
        )
        return encode_digest(resp["Mac"].hex())

    def verify(self, message: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(message), signature)


def signer_from_env(env: Mapping[str, str] | None = None, kms_client: Any = None) -> MarkerSigner:
    """`TOWER_ENV=local`: HMAC with `TOWER_LINE_ID_KEY` (base64); otherwise KMS `TOWER_KMS_HMAC_KEY_ID`."""
    env = os.environ if env is None else env
    if env.get("TOWER_ENV", "local") == "local":
        raw = env.get("TOWER_LINE_ID_KEY")
        if not raw:
            raise ValueError("TOWER_LINE_ID_KEY is not set")
        try:
            return HmacMarkerSigner(base64.b64decode(raw, validate=True))
        except (binascii.Error, ValueError) as e:
            raise ValueError("TOWER_LINE_ID_KEY is not base64") from e
    key_id = env.get("TOWER_KMS_HMAC_KEY_ID")
    if not key_id:
        raise ValueError("TOWER_KMS_HMAC_KEY_ID must be set when TOWER_ENV is not local")
    if kms_client is None:
        import boto3

        kms_client = boto3.client("kms")
    return KmsMarkerSigner(kms_client, key_id)


@dataclass(frozen=True)
class Marker:
    trimmed_at: datetime
    original_prev_hash: str
    signature: str

    def text(self) -> str:
        return f"trimmed|{_marker_ts(self.trimmed_at)}|{self.original_prev_hash}|{self.signature}"


def _marker_ts(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _marker_message(line_id: str, ts_seq: str, trimmed_at: datetime, original_prev_hash: str) -> bytes:
    return "|".join((line_id, ts_seq, _marker_ts(trimmed_at), original_prev_hash)).encode("ascii")


def parse_marker(prev_hash: str) -> Marker | None:
    m = MARKER_RE.fullmatch(prev_hash)
    if not m:
        return None
    at = datetime.strptime(m["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return Marker(at, m["orig"], m["sig"])


def make_marker(
    signer: MarkerSigner, line_id: str, ts_seq: str, trimmed_at: datetime, original_prev_hash: str
) -> Marker:
    if original_prev_hash != GENESIS and parse_marker(original_prev_hash) is not None:
        raise ValueError("row is already a trim anchor")
    sig = signer.sign(_marker_message(line_id, ts_seq, trimmed_at, original_prev_hash))
    return Marker(trimmed_at.astimezone(UTC).replace(microsecond=0), original_prev_hash, sig)


def marker_valid(signer: MarkerSigner, record: AuditRecord) -> bool:
    marker = parse_marker(record.prev_hash) if record.prev_hash else None
    if marker is None:
        return False
    msg = _marker_message(record.line_id, record.ts_seq, marker.trimmed_at, marker.original_prev_hash)
    return signer.verify(msg, marker.signature)
