"""Pure pieces: signed values, invite-code normalisation, the mobile-data simulation switch, settings."""

from __future__ import annotations

import pytest
from binding_page import invite, mobile_data
from binding_page.config import Settings, carrier_env
from binding_page.session import Signer

from tests.privacy.patterns import phone_hits

pytestmark = pytest.mark.unit

S = Settings(session_secret="x" * 20)


def test_signer_round_trip_and_purpose_and_expiry() -> None:
    s = Signer("secret-secret-secret")
    v = s.sign("state", {"u": "user-asish", "n": "abc"}, expires_at=1000)
    assert s.unsign("state", v, now=999) == {"u": "user-asish", "n": "abc", "p": "state", "x": 1000}
    assert s.unsign("state", v, now=1000) is None  # expired
    assert s.unsign("flow", v, now=0) is None  # wrong purpose
    assert Signer("another-secret-value").unsign("state", v, now=0) is None
    assert s.unsign("state", "garbage", now=0) is None
    assert s.unsign("state", None, now=0) is None


def test_tampered_payload_is_refused() -> None:
    s = Signer("secret-secret-secret")
    v = s.sign("session", {"u": "user-mom"}, expires_at=10**10)
    body, mac = v.split(".")
    forged = s.sign("session", {"u": "user-asish"}, expires_at=10**10).split(".")[0]
    assert s.unsign("session", f"{forged}.{mac}", now=0) is None
    assert s.unsign("session", f"{body}.{mac}", now=0) is not None


def test_signatures_and_csrf_are_never_phone_shaped() -> None:
    s = Signer("secret-secret-secret")
    for i in range(300):
        v = s.sign("session", {"u": f"user-{i}"}, expires_at=2_000_000_000)
        assert not any(ch.isdigit() for ch in v.split(".")[1])
        assert phone_hits(s.csrf(v)) == []
        assert s.check_csrf(v, s.csrf(v)) and not s.check_csrf(v, "") and not s.check_csrf(v, None)


def test_invite_normalise() -> None:
    assert invite.normalize("abcd-efgh") == "ABCDEFGH"
    assert invite.normalize(" ABCD EFGH ") == "ABCDEFGH"
    assert invite.normalize("ABCDEFG") is None
    assert invite.normalize("ABCD-EFGI") is None  # I is not in the alphabet
    assert invite.normalize("+16135550101") is None
    assert invite.display("ABCDEFGH") == "ABCD-EFGH"


def test_mobile_data_local_and_aws() -> None:
    aws = Settings(session_secret="x" * 20, tower_env="aws")
    assert mobile_data.simulated_client_id(S, "phone-asish") == "phone-asish"
    assert mobile_data.simulated_client_id(S, None) is None
    assert mobile_data.simulated_client_id(aws, "phone-asish") is None
    assert mobile_data.simulated_client_id(aws, "+16135550101") is None  # inert: ignored, not even parsed
    for bad in ("+16135550101", "Phone", "phone asish", "x" * 41, "-phone"):
        with pytest.raises(mobile_data.SimulationParamRejected):
            mobile_data.simulated_client_id(S, bad)


def test_settings() -> None:
    assert S.redirect_uri == "http://localhost:8081/bind/callback"
    assert S.admin_enabled is False
    assert Settings(session_secret="x" * 20, admin=True).admin_enabled is True
    assert Settings(session_secret="x" * 20, admin=True, tower_env="aws").admin_enabled is False
    assert Settings(session_secret="x" * 20, base_url="https://bind.example/").secure_cookies is True
    with pytest.raises(ValueError):
        Settings(session_secret="")
    env = Settings.from_env({"SESSION_SECRET": "y" * 20, "BIND_ADMIN": "1", "BASE_URL": "http://h:1/"})
    assert (
        env.admin_enabled and env.base_url == "http://h:1" and env.redirect_uri == "http://h:1/bind/callback"
    )


def test_carrier_env_defaults() -> None:
    e = carrier_env({"CARRIER_CLIENT_ID": "custom"})
    assert e["CARRIER_CLIENT_ID"] == "custom"
    assert e["CARRIER_SCOPES"] == "number-verification" and e["CARRIER_PROFILE"] == "proactive"
