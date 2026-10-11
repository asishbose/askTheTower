"""`line_id` HMAC and `msisdn_enc` envelope encryption.

Two protocols, two implementations each:

- `LocalLineIdHasher` / `LocalMsisdnCipher` — keys from the environment, used when `TOWER_ENV=local` and in tests.
- `KmsLineIdHasher` / `KmsMsisdnCipher` — thin KMS wrappers (GenerateMac on an HMAC_256 key; GenerateDataKey
  envelope); prompt 13 wires the key ids.

Encodings are chosen so that neither a `line_id` nor a ciphertext can ever match the privacy regex
`\\+?\\d{10,15}`: `line_id` is the HMAC digest in hex re-lettered to `a`-`p` (no digits at all), ciphertexts are
URL-safe base64 (a 10-digit run is ~1e-8 per position, and the privacy test asserts it).

Nothing in this module logs. Decrypted numbers exist only as return values.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tower_consent.errors import CryptoError, InvalidE164

if TYPE_CHECKING:
    from mypy_boto3_kms import KMSClient

E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
LINE_ID_PREFIX = "ln_"
_HEX_TO_LETTERS = str.maketrans("0123456789abcdef", "abcdefghijklmnop")
_AAD = b"tower-consent/msisdn/v1"
_LOCAL_PREFIX = "v1."
_KMS_PREFIX = "k1."
_ENC_CONTEXT = {"purpose": "tower-msisdn"}


def validate_e164(e164: str) -> str:
    """Return `e164` unchanged if it is E.164; raise `InvalidE164` (without echoing the value) otherwise."""
    if not isinstance(e164, str) or not E164_RE.match(e164):
        raise InvalidE164("value is not an E.164 number")
    return e164


def _encode_line_id(digest: bytes) -> str:
    return LINE_ID_PREFIX + digest.hex().translate(_HEX_TO_LETTERS)


def is_line_id(value: str) -> bool:
    return bool(re.fullmatch(r"ln_[a-p]{64}", value))


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as e:
        raise CryptoError("ciphertext is not valid base64") from e


@runtime_checkable
class LineIdHasher(Protocol):
    def line_id(self, e164: str) -> str:
        """HMAC-SHA256 over the E.164 string; stable for a given key."""
        ...


@runtime_checkable
class MsisdnCipher(Protocol):
    def encrypt(self, e164: str) -> str:
        """Encrypt; the result differs on every call (random nonce)."""
        ...

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt in memory. Never logged, never returned to a caller outside the carrier/SNS path."""
        ...


def _key_bytes(raw: bytes, what: str) -> bytes:
    if len(raw) < 32:
        raise CryptoError(f"{what} must be at least 32 bytes")
    return raw


class LocalLineIdHasher:
    def __init__(self, key: bytes) -> None:
        self._key = _key_bytes(key, "line-id key")

    def __repr__(self) -> str:
        return "LocalLineIdHasher(key=<redacted>)"

    def line_id(self, e164: str) -> str:
        validate_e164(e164)
        return _encode_line_id(hmac.new(self._key, e164.encode("ascii"), hashlib.sha256).digest())


class LocalMsisdnCipher:
    """AES-256-GCM with a local key. Output: `v1.` + base64url(nonce || ciphertext+tag)."""

    def __init__(self, key: bytes) -> None:
        self._aead = AESGCM(_key_bytes(key, "msisdn key")[:32])

    def __repr__(self) -> str:
        return "LocalMsisdnCipher(key=<redacted>)"

    def encrypt(self, e164: str) -> str:
        validate_e164(e164)
        nonce = os.urandom(12)
        return _LOCAL_PREFIX + _b64e(nonce + self._aead.encrypt(nonce, e164.encode("ascii"), _AAD))

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(_LOCAL_PREFIX):
            raise CryptoError("not a local-cipher ciphertext")
        raw = _b64d(ciphertext[len(_LOCAL_PREFIX) :])
        try:
            return self._aead.decrypt(raw[:12], raw[12:], _AAD).decode("ascii")
        except (InvalidTag, ValueError) as e:
            raise CryptoError("msisdn_enc failed authentication") from e


class KmsLineIdHasher:
    """GenerateMac on a KMS HMAC_256 key: the HMAC key never leaves KMS."""

    def __init__(self, kms: KMSClient, key_id: str) -> None:
        self._kms = kms
        self._key_id = key_id

    def __repr__(self) -> str:
        return "KmsLineIdHasher(key_id=<configured>)"

    def line_id(self, e164: str) -> str:
        validate_e164(e164)
        resp = self._kms.generate_mac(
            KeyId=self._key_id, Message=e164.encode("ascii"), MacAlgorithm="HMAC_SHA_256"
        )
        return _encode_line_id(resp["Mac"])


class KmsMsisdnCipher:
    """KMS envelope: a fresh data key per value; output `k1.` + base64url(len16(blob) || blob || nonce || ct)."""

    def __init__(self, kms: KMSClient, key_id: str) -> None:
        self._kms = kms
        self._key_id = key_id

    def __repr__(self) -> str:
        return "KmsMsisdnCipher(key_id=<configured>)"

    def encrypt(self, e164: str) -> str:
        validate_e164(e164)
        dk = self._kms.generate_data_key(
            KeyId=self._key_id, KeySpec="AES_256", EncryptionContext=_ENC_CONTEXT
        )
        blob: bytes = dk["CiphertextBlob"]
        nonce = os.urandom(12)
        ct = AESGCM(dk["Plaintext"]).encrypt(nonce, e164.encode("ascii"), _AAD)
        return _KMS_PREFIX + _b64e(len(blob).to_bytes(2, "big") + blob + nonce + ct)

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(_KMS_PREFIX):
            raise CryptoError("not a KMS-envelope ciphertext")
        raw = _b64d(ciphertext[len(_KMS_PREFIX) :])
        n = int.from_bytes(raw[:2], "big")
        blob, rest = raw[2 : 2 + n], raw[2 + n :]
        try:
            key = self._kms.decrypt(CiphertextBlob=blob, EncryptionContext=_ENC_CONTEXT)["Plaintext"]
            return AESGCM(key).decrypt(rest[:12], rest[12:], _AAD).decode("ascii")
        except (InvalidTag, ValueError) as e:
            raise CryptoError("msisdn_enc failed authentication") from e


def _env_key(env: Mapping[str, str], name: str) -> bytes:
    value = env.get(name)
    if not value:
        raise CryptoError(f"{name} is not set")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise CryptoError(f"{name} is not base64") from e


def crypto_from_env(
    env: Mapping[str, str] | None = None, kms_client: Any = None
) -> tuple[LineIdHasher, MsisdnCipher]:
    """Pick the implementation from the environment.

    `TOWER_ENV=local`: `TOWER_LINE_ID_KEY` and `TOWER_MSISDN_KEY` (base64, >= 32 bytes each).
    Otherwise: `TOWER_KMS_HMAC_KEY_ID` (HMAC_256 key) and `TOWER_KMS_KEY_ID` (symmetric key), via `kms_client`
    (a boto3 KMS client; created from the default session if omitted).
    """
    env = os.environ if env is None else env
    if env.get("TOWER_ENV", "local") == "local":
        return (
            LocalLineIdHasher(_env_key(env, "TOWER_LINE_ID_KEY")),
            LocalMsisdnCipher(_env_key(env, "TOWER_MSISDN_KEY")),
        )
    if kms_client is None:
        import boto3

        kms_client = boto3.client("kms")
    hmac_key, enc_key = env.get("TOWER_KMS_HMAC_KEY_ID"), env.get("TOWER_KMS_KEY_ID")
    if not hmac_key or not enc_key:
        raise CryptoError(
            "TOWER_KMS_HMAC_KEY_ID and TOWER_KMS_KEY_ID must be set when TOWER_ENV is not local"
        )
    return KmsLineIdHasher(kms_client, hmac_key), KmsMsisdnCipher(kms_client, enc_key)
