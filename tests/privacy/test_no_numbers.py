"""Privacy grep: nothing that looks like a phone number, a health word, a key or a bearer in generated output."""

from pathlib import Path

import pytest

from tests.privacy.patterns import AWS_KEY_ID, BEARER, HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [ROOT / "artifacts", ROOT / "deploy" / "compose" / "logs"]
SCAN_GLOBS = ["**/*.md", "**/*.json", "**/*.log", "**/*.txt", "**/*.jsonl"]
EXEMPT = {"policy-table.md"}  # contains the regex itself in its header
# Third-party package inventories from `make sbom` / `make scan` (package ids, SHA digests, CVE ids): not our
# runtime output, so only the key/bearer check applies — the same split as scripts/secrets_grep.py.
NO_PHONE_CHECK = (ROOT / "artifacts" / "sbom", ROOT / "artifacts" / "scan")


def _files():
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for g in SCAN_GLOBS:
            for f in d.glob(g):
                if f.name not in EXEMPT:
                    yield f


def test_no_phone_numbers_in_generated_output() -> None:
    files = [f for f in _files() if not any(f.is_relative_to(d) for d in NO_PHONE_CHECK)]
    bad = {str(f): phone_hits(f.read_text(errors="ignore")) for f in files}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"phone-number-shaped strings in generated output: {bad}"


def test_no_health_words_in_transcripts_or_sms() -> None:
    bad = {}
    for f in _files():
        if "transcript" in str(f) or "sms" in f.name:
            hits = HEALTH_WORDS.findall(f.read_text(errors="ignore"))
            if hits:
                bad[str(f)] = hits
    assert not bad, f"health words in spoken/SMS output: {bad}"


def test_no_keys_or_bearers() -> None:
    bad = {}
    for f in _files():
        t = f.read_text(errors="ignore")
        if AWS_KEY_ID.search(t) or BEARER.search(t):
            bad[str(f)] = True
    assert not bad, f"credentials in generated output: {list(bad)}"
