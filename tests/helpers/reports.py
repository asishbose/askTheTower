"""Parsers for the evidence files the suite writes, shared by the integration gates (tests/integration/*) and
`scripts/test_report.py`: junit XML, Cobertura coverage XML, the latency tables, the conformance report."""

from __future__ import annotations

import re
import tomllib
import xml.etree.ElementTree as ET  # noqa: S405 - our own pytest/coverage output, not untrusted input
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
P95_GATE_MS = 400.0


@dataclass
class Junit:
    path: Path
    tests: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)


def read_junit(path: Path) -> Junit:
    root = ET.parse(path).getroot()  # noqa: S314
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    j = Junit(path)
    for s in suites:
        j.seconds += float(s.get("time", 0) or 0)
        for case in s.iter("testcase"):
            j.tests += 1
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            if case.find("failure") is not None:
                j.failed += 1
                j.failures.append(name)
            elif case.find("error") is not None:
                j.errors += 1
                j.failures.append(name + " (error)")
            elif (sk := case.find("skipped")) is not None:
                j.skipped += 1
                reason = re.sub(r"^Skipped: ", "", sk.get("message", "") or "")[:140]
                j.skip_reasons[reason] = j.skip_reasons.get(reason, 0) + 1
            else:
                j.passed += 1
    return j


def coverage_gates() -> dict[str, float]:
    """`[tool.att.coverage-gates]` in pyproject.toml: {path prefix: minimum line-rate %}."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {k: float(v) for k, v in data["tool"]["att"]["coverage-gates"].items()}


def coverage_by_prefix(path: Path, prefixes: list[str]) -> dict[str, tuple[int, int]]:
    """Cobertura XML → {prefix: (covered lines, valid lines)} summed over files under each prefix."""
    root = ET.parse(path).getroot()  # noqa: S314
    sources = [Path(s.text or "") for s in root.iter("source")]
    out = {p: (0, 0) for p in prefixes}
    for cls in root.iter("class"):
        fname = cls.get("filename", "")
        full = next((str((src / fname).resolve()) for src in sources if (src / fname).exists()), fname)
        rel = Path(full).resolve().as_posix()
        rel = rel.split(ROOT.resolve().as_posix() + "/", 1)[-1]
        lines = cls.find("lines")
        if lines is None:
            continue
        valid = len(lines.findall("line"))
        hit = sum(1 for ln in lines.findall("line") if int(ln.get("hits", "0")) > 0)
        for p in prefixes:
            if rel.startswith(p.rstrip("/") + "/"):
                c, v = out[p]
                out[p] = (c + hit, v + valid)
    return out


@dataclass(frozen=True)
class LatencyRow:
    source: str
    path: str
    p50: float
    p95: float
    gated: bool
    verdict: str


def read_latency(path: Path) -> list[LatencyRow]:
    """The `| Path | n | p50 | p95 | ... | Gate |` rows of a latency table (scripts/latency.py output)."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or not re.fullmatch(r"\d+(\.\d+)?", cells[3] or "x"):
            continue
        verdict = cells[-1]
        rows.append(
            LatencyRow(
                path.name,
                cells[0],
                float(cells[2]),
                float(cells[3]),
                verdict in ("pass", "FAIL", "**FAIL**"),
                verdict,
            )
        )
    return rows


@dataclass(frozen=True)
class Conformance:
    operations: int
    cases: int
    failed_operations: int


def read_conformance(path: Path) -> Conformance:
    m = re.search(
        r"(\d+) operations, (\d+) generated cases, (\d+) operations with failures", path.read_text("utf-8")
    )
    if not m:
        raise ValueError(f"{path}: no summary line")
    return Conformance(int(m[1]), int(m[2]), int(m[3]))
