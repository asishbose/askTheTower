"""Prompt 20 acceptance: no E.164, no key and no bearer in any new file of the web chat path (the page, the
proxy, the agent's HTTP app, the scripts, the Terraform modules, the env template). Health words: none in what
a user reads (the page) or what the agent may say (the phrase table)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.privacy.patterns import AWS_KEY_ID, BEARER, HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
NEW_SOURCES = [
    *sorted(
        p
        for p in (ROOT / "services" / "web-chat").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and "tests" not in p.parts
    ),
    *(
        ROOT / "services" / "ref-client" / "src" / "ref_client" / f
        for f in ("http.py", "auth.py", "phrasebook.py")
    ),
    ROOT / "services" / "ref-client" / ".env.example",
    *(ROOT / "scripts" / f for f in ("cognito_user.py", "aws_seed.py", "web_chat_sync.py")),
    *sorted((ROOT / "deploy" / "terraform" / "modules" / "cognito").glob("*.tf")),
    *sorted((ROOT / "deploy" / "terraform" / "modules" / "web_chat").glob("*.tf")),
]
SAID = {"index.html", "app.js", "phrasebook.py"}


@pytest.mark.parametrize("path", NEW_SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_number_key_or_bearer_in_new_files(path: Path) -> None:
    assert path.is_file(), path
    body = path.read_text(encoding="utf-8")
    assert not phone_hits(body), phone_hits(body)
    assert not AWS_KEY_ID.search(body) and not BEARER.search(body) and not PRIVATE_KEY.search(body)
    if path.name in SAID:
        assert not HEALTH_WORDS.search(body), HEALTH_WORDS.findall(body)
