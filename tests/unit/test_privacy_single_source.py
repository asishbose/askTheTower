"""Prompt 18 guardrail: the privacy regexes live in one module (`tests/privacy/patterns.py`, re-exported by
`tests/helpers/patterns.py`). No test, conftest or script defines its own phone-number or health-word pattern.
The phone-number rule (decision 1, 2026-10-11) is defined there too; its one runtime copy is
`ref_client/privacy.py`, which the demo UI's filter calls and `tests/privacy/test_phone_rule.py` holds identical.

Product code under `src/` cannot import `tests/` (images ship without it), so the runtime copies are listed
here by name and must stay *the same or stricter*: the ref-client's transcript redactor is asserted identical to
`E164_STRICT` (services/ref-client/tests/test_transcripts.py); Tower's auth and the audit record refuse any
run of 10+ digits, which is a superset of what `E164_STRICT` matches."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.privacy.patterns import E164_STRICT, HEALTH_WORDS

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
HOME = ROOT / "tests" / "privacy" / "patterns.py"
SHARED_RULE = ROOT / "services" / "ref-client" / "src" / "ref_client" / "privacy.py"
# A compiled pattern that looks for long digit runs (a phone number) or names a health word.
DEFINES = re.compile(
    r"re\.compile\((?P<arg>[^\n]*?(\\d\{(?:[89]|1\d)|\\d\[[^\]]*\]\{\d|emergency|unwell|fallen|911)[^\n]*)\)"
)
RUNTIME_COPIES = {
    "services/ref-client/src/ref_client/transcript.py": r"\+?\d{10,15}",
    "services/tower-mcp/src/tower_mcp/auth.py": r"\d{10,}",
    "packages/tower-audit/src/tower_audit/record.py": r"\d{10,}",
}
SKIP_DIRS = {
    ".venv",
    ".git",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".terraform",
    "charts",
}
TOP_SKIP = {"artifacts", "specs", "prompts", "docs"}


def _python_files() -> list[Path]:
    """Every .py file of ours, pruning virtualenvs and caches while walking (rglob would walk .venv on /mnt/c)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = Path(dirpath).relative_to(ROOT).parts
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not (not rel and d in TOP_SKIP)]
        out += [Path(dirpath) / f for f in filenames if f.endswith(".py")]
    return out


def test_no_other_file_defines_a_privacy_regex() -> None:
    offenders: dict[str, list[str]] = {}
    for p in _python_files():
        if p in (HOME, SHARED_RULE):
            continue
        rel = p.relative_to(ROOT).as_posix()
        for m in DEFINES.finditer(p.read_text(encoding="utf-8", errors="replace")):
            if rel in RUNTIME_COPIES and RUNTIME_COPIES[rel] in m.group("arg"):
                continue
            offenders.setdefault(rel, []).append(m.group(0))
    assert not offenders, f"privacy regexes defined outside tests/privacy/patterns.py: {offenders}"


def test_the_one_module_defines_them() -> None:
    text = HOME.read_text(encoding="utf-8")
    assert "E164_STRICT = re.compile" in text and "HEALTH_WORDS = re.compile" in text
    assert E164_STRICT.pattern == r"\+?\d{10,15}"  # 00 conventions, verbatim
    for word in ("fall", "emergency", "unwell", "911"):  # 00 conventions: the four named health words
        assert HEALTH_WORDS.search(f"x {word} y"), word


def test_runtime_copies_are_at_least_as_strict() -> None:
    for rel, pattern in RUNTIME_COPIES.items():
        assert pattern in (ROOT / rel).read_text(encoding="utf-8"), rel
        rx = re.compile(pattern)
        for sample in ("+16135550101", "16135550101", "4155550123", "+442071838750"):
            assert E164_STRICT.search(sample) and rx.search(sample), (rel, sample)


def test_phone_hits_is_the_shared_rule() -> None:
    from ref_client import privacy

    from tests.privacy.patterns import phone_hits

    text = "call +44 20 7946 0958 or 6135550101 at 20261005141200"
    assert phone_hits(text) == privacy.phone_numbers(text) == ["+44 20 7946 0958", "6135550101"]
    for sample in ("+16135550101", "16135550101", "4155550123", "+442071838750"):
        assert phone_hits(sample) == [sample], sample


def test_helpers_reexport_is_the_same_object() -> None:
    from tests.helpers import patterns

    assert patterns.E164_STRICT is E164_STRICT and patterns.HEALTH_WORDS is HEALTH_WORDS
