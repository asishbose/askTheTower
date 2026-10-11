"""Identity (RUN-ALL Decisions): Bearer JWT → `sub`; local static bearer + X-Tower-User only when TOWER_ENV=local;
ids mapped to audit-safe opaque ids."""

from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from tower_audit.record import AuditRecord
from tower_mcp.auth import Authenticator, AuthError, safe_user_id, split_list

pytestmark = pytest.mark.unit

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    """Stands in for `jwt.PyJWKClient(TOWER_JWKS_URL)`."""

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return jwt.PyJWK.from_dict(
            jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True) | {"alg": "RS256"}
        )


def token(key: Any = KEY, **claims: Any) -> str:
    body = {
        "sub": "amzn1.account.AEXAMPLE",
        "exp": int(time.time()) + 300,
        "iss": "https://idp.test",
        "aud": "tower",
    } | claims
    return jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm="RS256")


def bearer(t: str, **extra: str) -> dict[str, str]:
    return {"authorization": f"Bearer {t}", **extra}


@pytest.fixture
def jwt_auth() -> Authenticator:
    return Authenticator(
        env="aws", local_bearer="static", issuer="https://idp.test", audience="tower", jwk_client=FakeJwks()
    )


async def test_jwt_sub_is_the_user(jwt_auth: Authenticator) -> None:
    ident = await jwt_auth.authenticate(bearer(token()))
    assert ident.user_id == "amzn1.account.AEXAMPLE" and ident.method == "jwt"


@pytest.mark.parametrize(
    "bad",
    [
        token(OTHER),
        token(exp=int(time.time()) - 10),
        token(aud="someone-else"),
        token(iss="https://evil.test"),
        token(sub=None),
        "not-a-jwt",
    ],
)
async def test_bad_jwts_are_refused(jwt_auth: Authenticator, bad: str) -> None:
    with pytest.raises(AuthError):
        await jwt_auth.authenticate(bearer(bad))


async def test_static_bearer_and_user_header_ignored_outside_local(jwt_auth: Authenticator) -> None:
    with pytest.raises(AuthError):
        await jwt_auth.authenticate(bearer("static", **{"x-tower-user": "user-asish"}))
    ident = await jwt_auth.authenticate(bearer(token(), **{"x-tower-user": "user-mom"}))
    assert ident.user_id == "amzn1.account.AEXAMPLE"


async def test_local_mode() -> None:
    auth = Authenticator(env="local", local_bearer="static")
    assert (
        await auth.authenticate(bearer("static", **{"x-tower-user": "user-asish"}))
    ).user_id == "user-asish"
    for headers in ({}, bearer("static"), bearer("nope", **{"x-tower-user": "user-asish"})):
        with pytest.raises(AuthError):
            await auth.authenticate(headers)


@pytest.mark.parametrize(
    "raw", ["+16135550101", "16135550101", "user 1", "a" * 200, "amzn1.account.12345678901"]
)
def test_unsafe_subjects_map_to_opaque_ids(raw: str) -> None:
    uid = safe_user_id(raw)
    assert uid.startswith("u_") and uid == safe_user_id(raw)
    assert not any(ch.isdigit() for ch in uid[2:])
    AuditRecord.model_validate(  # the audit log accepts it as an actor (it refuses digit-shaped ids)
        {
            "line_id": "ln_" + "a" * 64,
            "ts": "2026-10-05T14:00:00Z",
            "actor_user_id": uid,
            "tool": "line_is_ok",
            "trigger": "voice",
            "outcome": "ok",
            "reason_codes": ["OK"],
            "policy_version": "a" * 64,
        }
    )


def test_safe_subjects_pass_through() -> None:
    assert safe_user_id("user-asish") == "user-asish"
    assert safe_user_id("amzn1.account.AEXAMPLE") == "amzn1.account.AEXAMPLE"


# --- prompt 15: the Alexa+ account-linking token ---------------------------------------------------------------
# Terraform hands Tower the Runtime authorizer's lists joined with "," (deploy/terraform/main.tf); OAuth access
# tokens from Amazon Cognito (the IdP registration.md uses) carry `client_id` and no `aud`.


def test_split_list() -> None:
    assert split_list(None) == [] and split_list("") == [] and split_list(" , ") == []
    assert split_list("a, b,,c ") == ["a", "b", "c"]
    assert split_list(["a", " b ", ""]) == ["a", "b"]


async def test_audience_may_be_a_comma_separated_list() -> None:
    auth = Authenticator(env="aws", audience="ask-the-tower, tower", jwk_client=FakeJwks())
    assert (await auth.authenticate(bearer(token(aud="tower")))).method == "jwt"
    assert (await auth.authenticate(bearer(token(aud="ask-the-tower")))).method == "jwt"
    with pytest.raises(AuthError):
        await auth.authenticate(bearer(token(aud="someone-else")))
    with pytest.raises(AuthError):
        await auth.authenticate(bearer(token(aud=None)))


@pytest.fixture
def cognito_auth() -> Authenticator:
    return Authenticator(
        env="aws", issuer="https://idp.test", client_ids="alexa-link-client,other", jwk_client=FakeJwks()
    )


async def test_access_token_with_client_id_and_no_aud(cognito_auth: Authenticator) -> None:
    ident = await cognito_auth.authenticate(
        bearer(token(aud=None, client_id="alexa-link-client", sub="3f2a9c1e-7b4d-4e8a-9c1f-a2b3c4d5e6f7"))
    )
    assert ident.user_id == "3f2a9c1e-7b4d-4e8a-9c1f-a2b3c4d5e6f7" and ident.method == "jwt"


@pytest.mark.parametrize("client_id", ["someone-else", None])
async def test_client_id_outside_the_list_is_refused(
    cognito_auth: Authenticator, client_id: str | None
) -> None:
    with pytest.raises(AuthError):
        await cognito_auth.authenticate(bearer(token(aud=None, client_id=client_id)))


async def test_audience_and_client_ids_both_enforced_when_both_set() -> None:
    auth = Authenticator(env="aws", audience="tower", client_ids="alexa-link-client", jwk_client=FakeJwks())
    assert (await auth.authenticate(bearer(token(client_id="alexa-link-client")))).method == "jwt"
    for bad in (token(), token(aud="x", client_id="alexa-link-client")):
        with pytest.raises(AuthError):
            await auth.authenticate(bearer(bad))


async def test_local_mode_also_accepts_the_alexa_jwt() -> None:
    """Alexa+ simulator → tunnel → local compose Tower (registration.md, local path): the linked user's JWT
    works next to the static bearer, and X-Tower-User never overrides `sub`."""
    auth = Authenticator(
        env="local", local_bearer="static", client_ids="alexa-link-client", jwk_client=FakeJwks()
    )
    ident = await auth.authenticate(
        bearer(token(aud=None, client_id="alexa-link-client"), **{"x-tower-user": "user-mom"})
    )
    assert ident.user_id == "amzn1.account.AEXAMPLE" and ident.method == "jwt"
