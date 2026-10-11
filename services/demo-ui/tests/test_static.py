"""Doc 11 §8.1, §8.2, §8.8, §11 "No second policy path": the UI imports no policy or carrier code, names no CAMARA
path and no Gateway variable, and its chips come only from Tower's codes. The other half of the import graph
(nothing imports `demo_ui`; no product imports `ref_client`) is `services/ref-client/tests/test_fallback.py`.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SERVICE = Path(__file__).resolve().parents[1]
SRC = SERVICE / "src" / "demo_ui"
ROOT = SERVICE.parents[1]
# Decides (policy) or reaches the carrier, Tower's internals, Alerts or the page's code: the UI talks HTTP to them.
FORBIDDEN = ("tower_policy", "camara_client", "mock_carrier", "tower_mcp", "alerts", "binding_page")
SOURCES = sorted(
    p for p in SRC.rglob("*") if p.suffix in {".py", ".html", ".css"} and "__pycache__" not in p.parts
)


def camara_bases() -> list[str]:
    """`/sim-swap/v2`, `/call-forwarding-signal/v0.4`, …: the server paths of every vendored spec."""
    bases = []
    for spec in sorted((ROOT / "specs" / "camara").glob("*.yaml")):
        for server in yaml.safe_load(spec.read_text(encoding="utf-8")).get("servers", []):
            bases.append(str(server["url"]).replace("{apiRoot}", ""))
    return bases


def imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.unit
def test_the_ui_imports_no_policy_or_carrier_code() -> None:
    offenders = {
        str(p.relative_to(SERVICE)): sorted(imported_roots(p) & set(FORBIDDEN))
        for p in SOURCES
        if p.suffix == ".py"
    }
    assert {k: v for k, v in offenders.items() if v} == {}
    assert SOURCES and any(p.name == "runner.py" for p in SOURCES)


@pytest.mark.unit
def test_no_camara_path_no_gateway_and_no_policy_call_in_the_source() -> None:
    bases = camara_bases()
    assert len(bases) == 6 and "/sim-swap/v2" in bases
    # the Gateway's settings are CARRIER_GATEWAY_URL/_AUTH/_TOOLS/_REGION/_SESSION (02, 05)
    needles = [*bases, "/oauth2/", "CARRIER_GATEWAY_", "evaluate_line", "phrase(", "msisdn"]
    hits = {
        f"{p.relative_to(SERVICE)}: {n}"
        for p in SOURCES
        for n in needles
        if n in p.read_text(encoding="utf-8")
    }
    assert hits == set()


@pytest.mark.unit
def test_boto3_is_built_only_in_the_composition_root() -> None:
    """Clean-code rule (dependency inversion): `boto3.client(...)` only in deps.py."""
    users = sorted(p.name for p in SOURCES if p.suffix == ".py" and "boto3" in imported_roots(p))
    assert users == ["deps.py"]


@pytest.mark.integration
def test_a_fresh_process_loads_no_carrier_tower_or_alerts_code() -> None:
    """Transitively too, in a clean interpreter (the test session has imported half the repo): importing every
    `demo_ui` module brings in no carrier client, no Tower, no Alerts and no binding-page code. `tower_policy`'s
    types arrive through `tower_consent`/`tower_audit` (their record shapes); the UI itself never imports it."""
    code = (
        "import json, sys\n"
        "import demo_ui.app, demo_ui.deps, demo_ui.feed, demo_ui.runner, demo_ui.views, demo_ui.clients\n"
        "import demo_ui.__main__\n"
        "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules})))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)  # noqa: S603 - our own interpreter, fixed code
    loaded = set(json.loads(r.stdout.strip().splitlines()[-1]))
    assert loaded & {"camara_client", "mock_carrier", "tower_mcp", "alerts", "binding_page"} == set()
    assert "ref_client" in loaded  # the one library it may use (09 §4)
