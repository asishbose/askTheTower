"""Every CAMARA error code in `specs/camara/*.yaml` maps to exactly one ReasonCode (05 §5); unknown codes
fall back by status; the generated spec index and fixtures are in step with the specs."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from camara_client import ERROR_MAP, CarrierConfig, map_error, resolve_secret_ref
from camara_client.errors import CarrierError
from tower_policy import ReasonCode

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


def _gen() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gen_fixtures", ROOT / "packages" / "camara-client" / "scripts" / "gen_fixtures.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _gen()
OPERATION_PAIRS = {(int(p[0]), str(p[1])) for pairs in GEN.spec_error_codes().values() for p in pairs}
SPEC_PAIRS = sorted(OPERATION_PAIRS | GEN.component_error_codes())


def test_specs_declare_codes() -> None:
    assert len(SPEC_PAIRS) >= 20
    assert (404, "IDENTIFIER_NOT_FOUND") in OPERATION_PAIRS
    assert OPERATION_PAIRS <= set(SPEC_PAIRS)
    assert (410, "GONE") in SPEC_PAIRS  # callback-only, still mapped


@pytest.mark.parametrize(("status", "code"), SPEC_PAIRS, ids=[f"{s}-{c}" for s, c in SPEC_PAIRS])
def test_every_spec_code_is_mapped(status: int, code: str) -> None:
    assert (status, code) in ERROR_MAP, f"unmapped CAMARA error {status} {code}"
    err = map_error(status, code)
    assert isinstance(err.reason_code, ReasonCode)
    assert (err.status, err.code) == (status, code)


def test_map_has_nothing_the_specs_do_not_declare() -> None:
    assert set(ERROR_MAP) == set(SPEC_PAIRS)


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (401, "UNAUTHENTICATED", ReasonCode.CARRIER_ERROR),
        (403, "PERMISSION_DENIED", ReasonCode.CARRIER_ERROR),
        (403, "NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK", ReasonCode.NOT_BOUND),
        (404, "IDENTIFIER_NOT_FOUND", ReasonCode.NOT_BOUND),
        (429, "TOO_MANY_REQUESTS", ReasonCode.STALE_DATA),
        (429, "QUOTA_EXCEEDED", ReasonCode.STALE_DATA),
        (503, "UNAVAILABLE", ReasonCode.CARRIER_ERROR),
    ],
)
def test_doc_rows(status: int, code: str, reason: ReasonCode) -> None:
    """The rows 05 §5 spells out."""
    assert map_error(status, code).reason_code == reason


@pytest.mark.parametrize(
    ("status", "code", "reason", "retryable"),
    [
        (400, "SOMETHING_NEW", ReasonCode.CARRIER_ERROR, False),
        (418, None, ReasonCode.CARRIER_ERROR, False),
        (404, "DEVICE_NOT_FOUND", ReasonCode.CARRIER_ERROR, False),  # Commonalities 0.4 name, gone in Fall25
        (429, None, ReasonCode.STALE_DATA, True),
        (500, "INTERNAL", ReasonCode.CARRIER_ERROR, True),
        (502, None, ReasonCode.CARRIER_ERROR, True),
    ],
)
def test_unknown_falls_back_by_status(
    status: int, code: str | None, reason: ReasonCode, retryable: bool
) -> None:
    err = map_error(status, code)
    assert (err.reason_code, err.retryable) == (reason, retryable)


def test_only_engine_reason_codes() -> None:
    assert {r for r, _ in ERROR_MAP.values()} <= {
        ReasonCode.CARRIER_ERROR,
        ReasonCode.NOT_BOUND,
        ReasonCode.STALE_DATA,
    }


def test_carrier_error_carries_no_text() -> None:
    err = map_error(404, "IDENTIFIER_NOT_FOUND")
    assert str(err) == "NOT_BOUND (404 IDENTIFIER_NOT_FOUND)"
    assert err == CarrierError(ReasonCode.NOT_BOUND, False, status=404, code="IDENTIFIER_NOT_FOUND")


def test_generated_index_and_fixtures_are_fresh() -> None:
    assert GEN.main(["--check"]) == 0, "run: uv run python packages/camara-client/scripts/gen_fixtures.py"


def test_config_from_env() -> None:
    cfg = CarrierConfig.from_env(
        {
            "CARRIER_BASE_URL": "https://mock-carrier.local:8443/",
            "CARRIER_CLIENT_ID": "alerts",
            "CARRIER_PROFILE": "proactive",
            "CARRIER_SCOPES": "sim-swap, device-reachability-status",
        }
    )
    assert cfg.base_url == "https://mock-carrier.local:8443"
    assert cfg.oauth.token_url == "https://mock-carrier.local:8443/oauth2/token"
    assert cfg.oauth.scopes == ("sim-swap", "device-reachability-status")
    assert (cfg.client, cfg.backend, cfg.profile) == ("direct", "mock", "proactive")
    with pytest.raises(ValueError):
        CarrierConfig.from_env({"CARRIER_CLIENT": "gateway"})
    with pytest.raises(ValueError):
        CarrierConfig.from_env({"CARRIER_PROFILE": "fast"})


def test_secret_ref() -> None:
    assert resolve_secret_ref("env:X", {"X": "s3"}) == "s3"
    with pytest.raises(ValueError):
        resolve_secret_ref("env:X", {})
    with pytest.raises(ValueError):
        resolve_secret_ref("arn:aws:secretsmanager:...", {})
