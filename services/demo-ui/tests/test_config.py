"""Doc 11 §6 / decision 7: the token rule, ENV=aws degradation, personas. Never a value in an error message."""

from __future__ import annotations

import pytest
from demo_ui.config import ConfigError, Settings, is_loopback

pytestmark = pytest.mark.unit

ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - the address under test, never bound here


@pytest.mark.parametrize(
    ("host", "loop"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("localhost", True),
        (ALL_INTERFACES, False),
        ("192.168.1.20", False),
        ("demo.lan", False),
    ],
)
def test_is_loopback(host: str, loop: bool) -> None:
    assert is_loopback(host) is loop


def test_loopback_needs_no_token() -> None:
    assert Settings.from_env({"DEMO_UI_HOST": "127.0.0.1"}).ui_token is None


def test_lan_without_token_refuses_to_start() -> None:
    with pytest.raises(ConfigError, match="DEMO_UI_TOKEN") as e:
        Settings.from_env({"DEMO_UI_HOST": ALL_INTERFACES})
    assert ALL_INTERFACES not in str(e.value)


def test_lan_with_token_starts() -> None:
    s = Settings.from_env({"DEMO_UI_HOST": ALL_INTERFACES, "DEMO_UI_TOKEN": "t0k"})
    assert s.ui_token == "t0k" and "t0k" not in repr(s)


def test_container_published_on_loopback_needs_no_token() -> None:
    s = Settings.from_env({"DEMO_UI_HOST": ALL_INTERFACES, "DEMO_UI_PUBLISHED_LOOPBACK": "1"})
    assert s.published_loopback


def test_secrets_never_in_repr() -> None:
    s = Settings.from_env(
        {"TOWER_BEARER": "tb-secret", "MOCK_ADMIN_TOKEN": "mt-secret", "ALERTS_INTERNAL_BEARER": "ab-secret"}
    )
    assert not any(x in repr(s) for x in ("tb-secret", "mt-secret", "ab-secret"))


def test_local_defaults_enable_every_pane() -> None:
    s = Settings.from_env({})
    assert s.local and s.carrier_enabled and s.sms_enabled and s.binding_admin_enabled
    assert s.personas == ("asish", "mom")
    assert s.feed_poll_s == 2
    assert s.seed_path is not None and s.seed_path.name == "seed.py"


def test_aws_turns_off_carrier_macros_and_mom() -> None:
    s = Settings.from_env({"ENV": "aws", "MOCK_URL": "", "ALERTS_URL": "", "DYNAMO_ENDPOINT": ""})
    assert not s.carrier_enabled and not s.sms_enabled and not s.binding_admin_enabled
    assert s.personas == ("asish",)
    assert "local-only" in s.carrier_off_reason
    assert s.dynamo_endpoint is None and s.feed_poll_s == 5


def test_local_without_mock_url_says_why() -> None:
    s = Settings.from_env({"MOCK_URL": ""})
    assert not s.carrier_enabled and "MOCK_URL" in s.carrier_off_reason


def test_dynamo_endpoint_alias() -> None:
    assert Settings.from_env({"DYNAMO_ENDPOINT": "http://ddb:8000"}).dynamo_endpoint == "http://ddb:8000"
    s = Settings.from_env({"DYNAMO_ENDPOINT": "http://a", "TOWER_DYNAMODB_ENDPOINT": "http://b"})
    assert s.dynamo_endpoint == "http://b"


@pytest.mark.parametrize("bad", [{"ENV": "prod"}, {"REF_AGENT": "gpt"}, {"FEED_POLL_S": "0"}])
def test_bad_values_refused(bad: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(bad)


def test_macros_wait_for_alerts_only_locally() -> None:
    """06 §11.4: the Transplant macro pauses after each clock jump Alerts acts on (ref_client.demo)."""
    assert Settings.from_env({"ENV": "local"}).alerts_settle_s == 2.5
    assert Settings.from_env({"ENV": "aws"}).alerts_settle_s == 0.0
    assert Settings.from_env({"ENV": "local", "DEMO_ALERTS_SETTLE_S": "0"}).alerts_settle_s == 0.0
    with pytest.raises(ConfigError, match="DEMO_ALERTS_SETTLE_S"):
        Settings.from_env({"ENV": "local", "DEMO_ALERTS_SETTLE_S": "-1"})
