"""`deploy/terraform/modules/lambdas/admin_gate/handler.py` (10 §2.1): allow iff `requestContext.http.sourceIp` is in
`ALLOWED_CIDRS` **and** `Authorization` is `Bearer <DEMO_ADMIN_BEARER>` (decision 1, 2026-10-11); address headers
ignored; IPv4 and IPv6; anything malformed or unset denies (fail closed). Offline, stdlib only."""

from __future__ import annotations

import importlib.util
import json
import sys
from types import ModuleType
from typing import Any

import pytest

from tests.aws.conftest import TF

pytestmark = pytest.mark.unit


def _load() -> ModuleType:
    path = TF / "modules" / "lambdas" / "admin_gate" / "handler.py"
    spec = importlib.util.spec_from_file_location("admin_gate_handler", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load()
CIDRS = json.dumps(["203.0.113.7/32", "198.51.100.0/24", "2001:db8:abcd::/48"])
ADMIN_BEARER = "demo-admin-bearer-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
INTERNAL_BEARER = "alerts-internal-bearer-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
RIGHT = {"authorization": f"Bearer {ADMIN_BEARER}"}


@pytest.fixture(autouse=True)
def _bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_ADMIN_BEARER", ADMIN_BEARER)


def event(source_ip: Any, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """An authorizer event; by default it carries the right bearer, so the address is what a test varies."""
    return {
        "version": "2.0",
        "type": "REQUEST",
        "headers": RIGHT if headers is None else headers,
        "requestContext": {"http": {"method": "GET", "path": "/internal/sent", "sourceIp": source_ip}},
    }


@pytest.mark.parametrize(
    ("ip", "allowed"),
    [
        ("203.0.113.7", True),
        ("203.0.113.8", False),
        ("198.51.100.200", True),
        ("198.51.101.1", False),
        ("2001:db8:abcd:12::1", True),
        ("2001:db8:abce::1", False),
        ("::ffff:203.0.113.7", True),  # IPv4-mapped IPv6 is the same address
        ("::ffff:203.0.113.9", False),
    ],
)
def test_allow_and_deny_by_source_ip(monkeypatch: pytest.MonkeyPatch, ip: str, allowed: bool) -> None:
    monkeypatch.setenv("ALLOWED_CIDRS", CIDRS)
    assert gate.handler(event(ip)) == {"isAuthorized": allowed}


def test_address_headers_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_CIDRS", CIDRS)
    spoof = {"x-forwarded-for": "203.0.113.7", "forwarded": "for=203.0.113.7", "x-real-ip": "203.0.113.7"}
    assert gate.handler(event("192.0.2.1", {**RIGHT, **spoof})) == {"isAuthorized": False}


@pytest.mark.parametrize(
    ("headers", "allowed"),
    [
        (RIGHT, True),
        ({"Authorization": f"Bearer {ADMIN_BEARER}"}, True),  # header names are case-insensitive
        ({}, False),  # no bearer
        ({"authorization": f"Bearer {INTERNAL_BEARER}"}, False),  # the Alerts internal bearer is never it
        ({"authorization": ADMIN_BEARER}, False),  # no `Bearer ` prefix
        ({"authorization": f"bearer {ADMIN_BEARER}"}, False),
        ({"authorization": f"Bearer {ADMIN_BEARER} "}, False),
        ({"authorization": f"Bearer {ADMIN_BEARER[:-1]}"}, False),
        ({"x-demo-admin": ADMIN_BEARER}, False),
    ],
)
def test_right_address_needs_the_demo_admin_bearer(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, str], allowed: bool
) -> None:
    monkeypatch.setenv("ALLOWED_CIDRS", CIDRS)
    assert gate.handler(event("203.0.113.7", headers)) == {"isAuthorized": allowed}


def test_right_bearer_from_the_wrong_address_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_CIDRS", CIDRS)
    assert gate.handler(event("192.0.2.1", RIGHT)) == {"isAuthorized": False}


@pytest.mark.parametrize("configured", [None, ""])
def test_unset_bearer_denies_everything(monkeypatch: pytest.MonkeyPatch, configured: str | None) -> None:
    monkeypatch.setenv("ALLOWED_CIDRS", CIDRS)
    if configured is None:
        monkeypatch.delenv("DEMO_ADMIN_BEARER", raising=False)
    else:
        monkeypatch.setenv("DEMO_ADMIN_BEARER", configured)
    for headers in (RIGHT, {"authorization": "Bearer "}, {}):
        assert gate.handler(event("203.0.113.7", headers)) == {"isAuthorized": False}


@pytest.mark.parametrize("headers", [None, [], "authorization: x", {1: "Bearer x"}, {"authorization": 42}])
def test_malformed_headers_deny(monkeypatch: pytest.MonkeyPatch, headers: Any) -> None:
    monkeypatch.setenv("ALLOWED_CIDRS", CIDRS)
    ev = event("203.0.113.7")
    ev["headers"] = headers
    assert gate.handler(ev) == {"isAuthorized": False}


def test_the_compare_is_constant_time() -> None:
    src = (TF / "modules" / "lambdas" / "admin_gate" / "handler.py").read_text("utf-8")
    assert "hmac.compare_digest(" in src and '== f"Bearer' not in src


@pytest.mark.parametrize("ip", [None, "", "not-an-ip", "203.0.113.7/32", "999.1.1.1", 42, ["203.0.113.7"]])
def test_malformed_source_denies(monkeypatch: pytest.MonkeyPatch, ip: Any) -> None:
    monkeypatch.setenv("ALLOWED_CIDRS", CIDRS)
    assert gate.handler(event(ip)) == {"isAuthorized": False}


def test_missing_request_context_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_CIDRS", CIDRS)
    assert gate.handler({}) == {"isAuthorized": False}
    assert gate.handler({"requestContext": None}) == {"isAuthorized": False}


@pytest.mark.parametrize(
    "raw", ["", "[]", "not json", '{"a": 1}', '["203.0.113.7/32", "garbage"]', '["300.1.1.1/32"]']
)
def test_malformed_or_empty_list_denies_everything(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("ALLOWED_CIDRS", raw)
    assert gate.handler(event("203.0.113.7")) == {"isAuthorized": False}


def test_unset_list_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOWED_CIDRS", raising=False)
    assert gate.handler(event("203.0.113.7")) == {"isAuthorized": False}


def test_pure_helpers() -> None:
    # host bits set are accepted (strict=False): "203.0.113.7/24" means that /24
    nets = gate.parse_cidrs('["10.1.2.3/24"]')
    assert gate.is_allowed("10.1.2.99", nets) and not gate.is_allowed("10.1.3.1", nets)
    assert gate.is_allowed("10.1.2.99", ()) is False


def test_standard_library_only() -> None:
    src = (TF / "modules" / "lambdas" / "admin_gate" / "handler.py").read_text("utf-8")
    imports = {
        line.split()[1].split(".")[0] for line in src.splitlines() if line.startswith(("import ", "from "))
    }
    assert imports <= {"__future__", "hmac", "ipaddress", "json", "os", "typing"}, imports
