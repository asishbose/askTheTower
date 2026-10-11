"""The vendored CloudEvents schemas match specs/camara/ (regenerate: scripts/vendor_schemas.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_vendored_schemas_up_to_date() -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "vendor_schemas.py"
    spec = importlib.util.spec_from_file_location("vendor_schemas", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--check"]) == 0
