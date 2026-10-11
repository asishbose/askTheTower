"""KMS as Terraform deploys it (modules/kms), exercised against moto: two keys — a symmetric key for the
`msisdn_enc` envelope and an HMAC_256 key for `line_id` and the audit trim marker — addressed by ARN, as the
services' environment (TOWER_KMS_KEY_ID / TOWER_KMS_HMAC_KEY_ID) carries them."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from tower_audit import KmsMarkerSigner, signer_from_env
from tower_consent import KmsLineIdHasher, KmsMsisdnCipher, crypto_from_env
from tower_consent.crypto import is_line_id

from tests.privacy.patterns import phone_hits

pytestmark = pytest.mark.unit

E164 = "+16135550101"  # scenarios/demo.yaml fiction (555-01xx)


@pytest.fixture(scope="module")
def kms_env() -> Iterator[tuple[Any, dict[str, str]]]:
    """Two keys and their aliases, made once for the module (each `mock_aws()` start costs ~10 s on /mnt/c);
    the tests only read them."""
    from moto import mock_aws

    with pytest.MonkeyPatch.context() as mp:
        for k, v in {
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_DEFAULT_REGION": "us-east-1",
        }.items():
            mp.setenv(k, v)
        with mock_aws():
            kms = boto3.client("kms", region_name="us-east-1")
            main = kms.create_key(KeySpec="SYMMETRIC_DEFAULT", KeyUsage="ENCRYPT_DECRYPT")["KeyMetadata"]
            hmac_key = kms.create_key(KeySpec="HMAC_256", KeyUsage="GENERATE_VERIFY_MAC")["KeyMetadata"]
            kms.create_alias(AliasName="alias/att-dev-main", TargetKeyId=main["KeyId"])
            kms.create_alias(AliasName="alias/att-dev-line-id-hmac", TargetKeyId=hmac_key["KeyId"])
            env = {
                "TOWER_ENV": "aws",
                "TOWER_KMS_KEY_ID": main["Arn"],
                "TOWER_KMS_HMAC_KEY_ID": hmac_key["Arn"],
            }
            try:
                yield kms, env
            finally:
                # A nested `mock_aws()` shares backend state with an outer one (the root conftest's session-wide
                # moto when tests/aws runs with the service suites), so leave no alias behind for the next run.
                for alias in ("alias/att-dev-main", "alias/att-dev-line-id-hmac"):
                    kms.delete_alias(AliasName=alias)
                for key in (main, hmac_key):
                    kms.schedule_key_deletion(KeyId=key["KeyId"], PendingWindowInDays=7)


def test_crypto_from_env_on_aws(kms_env: tuple[Any, dict[str, str]]) -> None:
    kms, env = kms_env
    hasher, cipher = crypto_from_env(env, kms_client=kms)
    assert isinstance(hasher, KmsLineIdHasher) and isinstance(cipher, KmsMsisdnCipher)
    ct = cipher.encrypt(E164)
    assert ct.startswith("k1.") and cipher.decrypt(ct) == E164 and not phone_hits(ct)
    try:
        lid = hasher.line_id(E164)
    except NotImplementedError:  # pragma: no cover - moto version
        pytest.skip("moto does not implement KMS GenerateMac in this version")
    assert is_line_id(lid) and lid == hasher.line_id(E164)
    assert "arn:" not in repr(hasher) + repr(cipher)  # key ids are not echoed either


def test_aliases_address_the_same_keys(kms_env: tuple[Any, dict[str, str]]) -> None:
    kms, env = kms_env
    by_arn = KmsMsisdnCipher(kms, env["TOWER_KMS_KEY_ID"])
    by_alias = KmsMsisdnCipher(kms, "alias/att-dev-main")
    assert by_arn.decrypt(by_alias.encrypt(E164)) == E164


def test_audit_marker_signer_uses_the_hmac_key(kms_env: tuple[Any, dict[str, str]]) -> None:
    """Prompt 07's deferred check: `signer_from_env` under TOWER_ENV=aws signs with KMS GenerateMac."""
    kms, env = kms_env
    signer = signer_from_env(env, kms_client=kms)
    assert isinstance(signer, KmsMarkerSigner)
    try:
        sig = signer.sign(b"x")
    except NotImplementedError:  # pragma: no cover
        pytest.skip("moto does not implement KMS GenerateMac in this version")
    assert signer.verify(b"x", sig) and not signer.verify(b"y", sig)
    assert not phone_hits(sig)
