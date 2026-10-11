"""Crypto: HMAC stable across processes, cipher round-trips, ciphertext differs per call, nothing logged."""

from __future__ import annotations

import base64
import logging
import subprocess
import sys

import boto3
import pytest
from tower_consent import (
    KmsLineIdHasher,
    KmsMsisdnCipher,
    LocalLineIdHasher,
    LocalMsisdnCipher,
    crypto_from_env,
)
from tower_consent.crypto import is_line_id
from tower_consent.errors import CryptoError, InvalidE164

from tests.privacy.patterns import E164_STRICT, phone_hits

pytestmark = pytest.mark.unit

E164 = "+15555550123"
KEY = b"k" * 32


def test_line_id_is_stable_across_processes() -> None:
    here = LocalLineIdHasher(KEY).line_id(E164)
    code = (
        f"from tower_consent import LocalLineIdHasher;print(LocalLineIdHasher(b'k' * 32).line_id('{E164}'))"
    )
    other = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)  # noqa: S603
    assert other.stdout.strip() == here
    assert is_line_id(here)


def test_line_id_depends_on_key_and_number() -> None:
    a = LocalLineIdHasher(KEY)
    assert a.line_id(E164) != a.line_id("+15555550124")
    assert a.line_id(E164) != LocalLineIdHasher(b"z" * 32).line_id(E164)


def test_line_id_never_looks_like_a_number() -> None:
    h = LocalLineIdHasher(KEY)
    for i in range(500):
        lid = h.line_id(f"+1555555{i:04d}")
        assert not E164_STRICT.search(lid)
        assert not any(c.isdigit() for c in lid)


def test_cipher_round_trips_and_differs_per_call() -> None:
    c = LocalMsisdnCipher(b"m" * 32)
    a, b = c.encrypt(E164), c.encrypt(E164)
    assert a != b
    assert c.decrypt(a) == E164 == c.decrypt(b)
    assert E164 not in a and E164.lstrip("+") not in a
    assert not phone_hits(a) and not phone_hits(b)


def test_cipher_rejects_tampering_and_wrong_key() -> None:
    c = LocalMsisdnCipher(b"m" * 32)
    ct = c.encrypt(E164)
    flipped = ct[:-2] + ("A" if ct[-2] != "A" else "B") + ct[-1]
    with pytest.raises(CryptoError):
        c.decrypt(flipped)
    with pytest.raises(CryptoError):
        LocalMsisdnCipher(b"n" * 32).decrypt(ct)
    with pytest.raises(CryptoError):
        c.decrypt("garbage")


def test_short_keys_rejected() -> None:
    with pytest.raises(CryptoError):
        LocalLineIdHasher(b"short")
    with pytest.raises(CryptoError):
        LocalMsisdnCipher(b"short")


@pytest.mark.parametrize("bad", ["5555550123", "+0123456789", "+1 555 555 0123", "", "+1555"])
def test_non_e164_rejected_without_echo(bad: str) -> None:
    with pytest.raises(InvalidE164) as ei:
        LocalLineIdHasher(KEY).line_id(bad)
    assert not bad or bad not in str(ei.value)
    with pytest.raises(InvalidE164):
        LocalMsisdnCipher(b"m" * 32).encrypt(bad)


def test_decrypt_never_logs(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]) -> None:
    caplog.set_level(logging.DEBUG)
    c = LocalMsisdnCipher(b"m" * 32)
    h = LocalLineIdHasher(KEY)
    ct = c.encrypt(E164)
    assert c.decrypt(ct) == E164
    h.line_id(E164)
    with pytest.raises(CryptoError):
        c.decrypt(ct[:-3] + "AAA")
    out = capsys.readouterr()
    assert not caplog.records
    assert E164 not in out.out + out.err
    for obj in (c, h):
        assert "k" * 32 not in repr(obj) and "m" * 32 not in repr(obj) and "redacted" in repr(obj)


def test_crypto_from_env_local() -> None:
    env = {
        "TOWER_ENV": "local",
        "TOWER_LINE_ID_KEY": base64.b64encode(KEY).decode(),
        "TOWER_MSISDN_KEY": base64.b64encode(b"m" * 32).decode(),
    }
    h, c = crypto_from_env(env)
    assert h.line_id(E164) == LocalLineIdHasher(KEY).line_id(E164)
    assert c.decrypt(c.encrypt(E164)) == E164
    with pytest.raises(CryptoError):
        crypto_from_env({"TOWER_ENV": "local"})
    with pytest.raises(CryptoError):
        crypto_from_env({"TOWER_ENV": "aws"}, kms_client=object())


def test_kms_implementations_against_moto() -> None:
    from moto import mock_aws

    with mock_aws():
        kms = boto3.client("kms", region_name="us-east-1")
        enc_key = kms.create_key(KeySpec="SYMMETRIC_DEFAULT")["KeyMetadata"]["KeyId"]
        cipher = KmsMsisdnCipher(kms, enc_key)
        a, b = cipher.encrypt(E164), cipher.encrypt(E164)
        assert a != b and a.startswith("k1.")
        assert cipher.decrypt(a) == E164
        assert not phone_hits(a)
        mac_key = kms.create_key(KeySpec="HMAC_256", KeyUsage="GENERATE_VERIFY_MAC")["KeyMetadata"]["KeyId"]
        hasher = KmsLineIdHasher(kms, mac_key)
        try:
            lid = hasher.line_id(E164)
        except NotImplementedError:  # pragma: no cover - depends on the moto version
            pytest.skip("moto does not implement KMS GenerateMac in this version")
        assert lid == hasher.line_id(E164) and is_line_id(lid)
