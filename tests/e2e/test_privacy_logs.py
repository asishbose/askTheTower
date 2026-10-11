"""After the demo: no phone number, AWS key, bearer token or health word anywhere in deploy/compose/logs/**
(the stack's own logs, saved by `make demo` / `make logs-save`, plus the seed and demo output)."""

from __future__ import annotations

import re

import pytest

from tests.e2e.helpers import LOGS, ROOT, Stack, make
from tests.privacy.patterns import AWS_KEY_ID, BEARER, HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def logs(stack: Stack) -> dict[str, str]:
    r = make("logs-save", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    files = {
        str(p.relative_to(ROOT)): p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(LOGS.rglob("*"))
        if p.is_file() and p.name != ".gitkeep"
    }
    assert any(name.endswith("stack.log") for name in files), "make logs-save wrote no stack.log"
    return files


def test_no_phone_numbers_in_logs(logs: dict[str, str]) -> None:
    hits = {name: phone_hits(text)[:5] for name, text in logs.items() if phone_hits(text)}
    assert not hits, f"phone-number-shaped strings in compose logs: {hits}"


def test_no_keys_or_bearers_in_logs(logs: dict[str, str]) -> None:
    secrets = _local_secrets()
    for name, text in logs.items():
        assert not AWS_KEY_ID.search(text), f"AWS key id in {name}"
        assert not BEARER.search(text), f"bearer token in {name}"
        leaked = [k for k, v in secrets.items() if v in text]
        assert not leaked, f"{name} contains the value of {leaked} from deploy/compose/.env"


def test_sms_bodies_have_no_health_words(logs: dict[str, str]) -> None:
    sms = [ln for text in logs.values() for ln in text.splitlines() if "SMS to=" in ln]
    assert sms, "no Alerts SMS line in the logs: the phone buzz of moment 3 did not happen"
    bad = [ln for ln in sms if HEALTH_WORDS.search(ln.split("body=", 1)[-1])]
    assert not bad, bad


def _local_secrets() -> dict[str, str]:
    env = ROOT / "deploy" / "compose" / ".env"
    if not env.exists():
        return {}
    keys = re.compile(
        r"^(TOWER_BEARER|INTERNAL_BEARER|TOWER_LINE_ID_KEY|TOWER_MSISDN_KEY|SESSION_SECRET|MOCK_JWT_SECRET|MOCK_ADMIN_TOKEN)=(.+)$"
    )
    return {
        m[1]: m[2] for line in env.read_text().splitlines() if (m := keys.match(line)) and len(m[2]) >= 16
    }
