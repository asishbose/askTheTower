"""Unit tests: clock, scenario parsing, JWT, scopes, error envelopes. No HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mock_carrier import errors
from mock_carrier.clock import Clock, iso, parse_iso, parse_offset
from mock_carrier.oauth import decode_jwt, encode_jwt, scope_covers
from mock_carrier.scenarios import ScenarioError, list_scenarios, load_scenario
from mock_carrier.state import line_ref

pytestmark = pytest.mark.unit

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


def test_clock_moves_only_when_told() -> None:
    c = Clock(datetime(2026, 10, 5, 14, tzinfo=UTC))
    assert c.now() == c.now()
    c.advance(720)
    assert iso(c.now()) == "2026-10-05T14:12:00.000Z"
    with pytest.raises(ValueError):
        Clock(datetime(2026, 1, 1))  # noqa: DTZ001 — naive on purpose


def test_parse_helpers() -> None:
    assert parse_iso("2026-10-05T14:00:00Z") == datetime(2026, 10, 5, 14, tzinfo=UTC)
    assert parse_offset("+00:12:00") == timedelta(minutes=12)
    assert parse_offset("+04:30") == timedelta(hours=4, minutes=30)
    for bad in ("00:12", "+1", "+a:b"):
        with pytest.raises(ValueError):
            parse_offset(bad)
    with pytest.raises(ValueError):
        parse_iso("2026-10-05T14:00:00")


def test_demo_scenario_is_08_section_2() -> None:
    sc = load_scenario(SCENARIOS, "demo")
    assert iso(sc.clock) == "2026-10-05T14:00:00.000Z"
    # Asish, Mom, and the transplant story's two contacts (06 §11.4: partner, neighbour)
    assert sorted(sc.lines) == ["+16135550101", "+16135550102", "+16135550103", "+16135550104"]
    assert sc.lines["+16135550103"].mobile_data_client_ids == ["phone-partner"]
    assert sc.lines["+16135550104"].mobile_data_client_ids == ["phone-neighbour"]
    asish = sc.lines["+16135550101"]
    assert asish.mobile_data_client_ids == ["phone-asish"] and asish.connectivity == ["DATA"]
    assert [(iso(e.at), e.line, e.event) for e in sc.timeline] == [
        ("2026-10-05T14:12:00.000Z", "+16135550101", "sim_swap"),
        ("2026-10-05T14:20:00.000Z", "+16135550101", "cf_set"),
    ]


def test_all_scenarios_load() -> None:
    names = list_scenarios(SCENARIOS)
    assert {"demo", "care", "transplant"} <= set(names)
    for name in names:
        load_scenario(SCENARIOS, name)
    assert len(load_scenario(SCENARIOS, "transplant:blip").timeline) == 4
    assert len(load_scenario(SCENARIOS, "care", "recovered").timeline) == 2


def test_scenario_errors(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError):
        load_scenario(SCENARIOS, "nope")
    with pytest.raises(ScenarioError):
        load_scenario(SCENARIOS, "demo", "nope")
    (tmp_path / "bad.yaml").write_text('clock: "2026-01-01T00:00:00Z"\nlines: {"16135550101": {}}\n')
    with pytest.raises(ScenarioError):
        load_scenario(tmp_path, "bad")
    (tmp_path / "bad2.yaml").write_text(
        'clock: "2026-01-01T00:00:00Z"\nlines: {"+16135550101": {}}\ntimeline: [{at: "+00:01", line: "+16135550101", event: explode}]\n'
    )
    with pytest.raises(ScenarioError):
        load_scenario(tmp_path, "bad2")


def test_jwt_roundtrip_and_tamper() -> None:
    tok = encode_jwt({"sub": "x", "exp": 1}, "k")
    assert decode_jwt(tok, "k") == {"sub": "x", "exp": 1}
    assert decode_jwt(tok, "other") is None
    h, p, s = tok.split(".")
    assert decode_jwt(f"{h}.{p}x.{s}", "k") is None
    assert decode_jwt("garbage", "k") is None


def test_scope_cover() -> None:
    assert scope_covers("sim-swap", "sim-swap:check")
    assert scope_covers("sim-swap:check", "sim-swap:check")
    assert not scope_covers("sim-swap", "sim-swap-subscriptions:read")
    assert not scope_covers("sim-swap:check", "sim-swap:retrieve-date")


def test_line_ref_is_opaque() -> None:
    ref = line_ref("+16135550101")
    assert "6135550101" not in ref and ref == line_ref("+16135550101") and ref.startswith("line:")


def test_error_envelope_shape() -> None:
    err = errors.UserNotAuthenticatedByMobileNetwork()
    assert err.body() == {
        "status": 403,
        "code": "NUMBER_VERIFICATION.USER_NOT_AUTHENTICATED_BY_MOBILE_NETWORK",
        "message": "Client must authenticate via the mobile network to use this service.",
    }
    resp = errors.envelope(errors.TooManyRequests(), correlator="c-1")
    assert resp.status_code == 429 and resp.headers["x-correlator"] == "c-1"


def test_every_error_code_in_the_specs_has_a_class() -> None:
    import yaml

    specs = Path(__file__).resolve().parents[3] / "specs" / "camara"
    codes: set[str] = set()
    for f in specs.glob("*.yaml"):
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        for resp in doc["components"].get("responses", {}).values():
            schema = resp.get("content", {}).get("application/json", {}).get("schema", {})
            for part in schema.get("allOf", []):
                codes |= set(part.get("properties", {}).get("code", {}).get("enum", []))
    classes = {c.code for c in errors.CamaraError.__subclasses__()}
    assert codes <= classes, codes - classes
