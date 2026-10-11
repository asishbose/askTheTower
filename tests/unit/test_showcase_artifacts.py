"""`make showcase-artifacts` (scripts/showcase_artifacts.py): staleness rules and the table splice."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "showcase_artifacts", ROOT / "scripts/showcase_artifacts.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module by name
    spec.loader.exec_module(mod)
    return mod


sa = _load()
CODE = datetime(2026, 10, 7, 0, 0, tzinfo=UTC)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(sa, "ROOT", tmp_path)
    (tmp_path / "artifacts").mkdir()
    return tmp_path


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).write_text(text, encoding="utf-8")


def test_dated_artefact_within_24h_is_fresh(root: Path) -> None:
    _write(root, "artifacts/a.md", "# x\n- **date:** 2026-10-06 01:00 UTC\n")
    s = sa.assess(sa.Artefact("artifacts/a.md", "measured", "cmd"), CODE)
    assert s.state == "fresh" and "date inside" in s.when


def test_dated_artefact_over_24h_older_than_code_is_stale(root: Path) -> None:
    _write(root, "artifacts/a.md", "# x\nat 2026-10-05 23:59 UTC\n")
    assert sa.assess(sa.Artefact("artifacts/a.md", "measured", "cmd"), CODE).state == "STALE"


def test_missing_generated_artefact_is_missing_but_missing_deferred_is_deferred(root: Path) -> None:
    assert sa.assess(sa.Artefact("artifacts/gone.md", "generated", "cmd"), CODE).state == "missing"
    assert sa.assess(sa.Artefact("artifacts/gone.png", "deferred", "cmd"), CODE).state == "deferred"


def test_not_run_placeholder_is_deferred_never_stale(root: Path) -> None:
    _write(root, "artifacts/eks.txt", "# EKS run — NOT RUN\n# 2020-01-01 00:00 UTC\n")
    s = sa.assess(sa.Artefact("artifacts/eks.txt", "deferred", "cmd"), CODE)
    assert s.state == "deferred" and "NOT RUN" in s.note


def test_regenerated_this_run_is_fresh_even_if_old(root: Path) -> None:
    _write(root, "artifacts/a.md", "at 2020-01-01 00:00 UTC\n")
    a = sa.Artefact("artifacts/a.md", "generated", "cmd")
    assert sa.assess(a, CODE, frozenset({"artifacts/a.md"})).state == "fresh"


def test_splice_appends_then_replaces_between_markers(tmp_path: Path) -> None:
    p = tmp_path / "doc.md"
    p.write_text("# Title\n\nhand-written\n", encoding="utf-8")
    sa.splice(p, sa.BEGIN, sa.END, "table v1")
    sa.splice(p, sa.BEGIN, sa.END, "table v2")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# Title\n\nhand-written\n")
    assert "table v2" in text and "table v1" not in text
    assert text.count(sa.BEGIN) == 1 and text.count(sa.END) == 1
