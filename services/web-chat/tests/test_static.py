"""09 §6.4 / §6.6: the static page. No secret in the config template; the `/bind/` prefix check is in `app.js`;
no phone number, framework, CDN, SMS or QR in any file; text is rendered as text."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.privacy.patterns import HEALTH_WORDS, phone_hits

pytestmark = pytest.mark.unit

PAGE = Path(__file__).resolve().parents[1]
FILES = sorted(p for p in PAGE.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
CONFIG_KEYS = {"COGNITO_DOMAIN", "CLIENT_ID", "REDIRECT_URI", "AGENT_URL", "BINDING_BASE_URL"}


def text(name: str) -> str:
    return (PAGE / name).read_text(encoding="utf-8")


def test_the_page_is_three_files_and_a_template() -> None:
    for name in ("index.html", "app.js", "styles.css", "config.js.example", "README.md", "proxy/handler.py"):
        assert (PAGE / name).is_file(), name
    assert not (PAGE / "package.json").exists()  # no bundler, no build step


def test_config_template_has_the_five_keys_and_no_secret() -> None:
    cfg = "\n".join(
        line for line in text("config.js.example").splitlines() if not line.lstrip().startswith("//")
    )
    keys = set(re.findall(r'"([A-Z_]+)":', cfg))
    assert keys == CONFIG_KEYS
    assert "LOCAL_BEARER" not in cfg and not re.search(r"(?i)secret|password|aws_|eyJ", cfg)
    values = re.findall(r'":\s*"([^"]*)"', cfg)
    assert values and all("<" in v for v in values)  # placeholders only


def test_app_js_checks_the_bind_prefix_and_builds_no_url() -> None:
    js = text("app.js")
    assert 'BINDING_BASE + "/bind/"' in js
    assert "url.startsWith(BIND_PREFIX)" in js
    assert 'step.kind !== "bind_line"' in js
    assert "NEXT_STEP_REJECTED" in js
    assert "a.href = step.url" in js  # the only link the page renders is the vetted next_step.url
    assert "innerHTML" not in js and "eval(" not in js  # text is set as text


def test_no_framework_cdn_sms_or_qr() -> None:
    html = text("index.html")
    assert re.findall(r'<script src="([^"]+)"', html) == ["config.js", "app.js"]
    assert "http://" not in html and "https://" not in html
    for name in ("index.html", "app.js"):
        body = text(name).lower()
        for word in ("sms", "qrcode", "qr code", "react", "vue", "jquery", "tel:"):
            assert word not in body, (name, word)


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(PAGE)))
def test_no_phone_number_or_health_word_in_any_file(path: Path) -> None:
    body = path.read_text(encoding="utf-8")
    assert not phone_hits(body)
    if path.suffix in {".html", ".js"}:
        assert not HEALTH_WORDS.search(body), HEALTH_WORDS.findall(body)


def test_binding_is_said_to_need_mobile_data() -> None:
    assert "Wi-Fi off" in text("index.html")  # rule 7: one tap on the phone over mobile data
