"""Conformance gate (testing-and-showcase §3): the mock passes schemathesis against the vendored CAMARA specs and the
recorded fixtures pass through both carrier clients. The work is done by the owning tests (linked below, all
`integration`); this module asserts they exist and that the report they write shows zero failing operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.reports import ARTIFACTS, read_conformance

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
OWNERS = {
    "services/mock-carrier/tests/test_conformance.py": "schemathesis vs specs/camara → artifacts/conformance-report.html",
    "services/mock-carrier/tests/test_openapi.py": "/openapi.json diffs clean against specs/camara",
    "packages/camara-client/tests/test_direct_mock.py": "fixtures through DirectClient → mock",
    "packages/camara-client/tests/test_gateway_fake.py": "fixtures through GatewayClient → fake Gateway → mock",
}
REPORT = ARTIFACTS / "conformance-report.html"


@pytest.mark.parametrize("path", sorted(OWNERS))
def test_conformance_tests_exist_and_are_integration(path: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "pytest.mark.integration" in text, f"{path} ({OWNERS[path]}) lost its integration marker"


def test_conformance_report_has_no_failures() -> None:
    if not REPORT.exists():
        pytest.fail(
            f"{REPORT} missing: services/mock-carrier/tests/test_conformance.py writes it (make test-integration)"
        )
    c = read_conformance(REPORT)
    assert c.operations >= 15 and c.cases > 0, c
    assert c.failed_operations == 0, f"{c.failed_operations} operations fail conformance — see {REPORT}"
