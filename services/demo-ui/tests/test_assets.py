"""Doc 11 §2 "Presentation" (asset rules) and §11 "Presentation (prompt 21)": the page's own files.

- No external request target in `templates/` or `static/`: every `src=`, `href=` (`<use href>` included), `url(`,
  `@import` and `fetch(` points at this origin. The SVG namespace (`xmlns`) and the OFL licence text name http URLs;
  neither is a request, so the check reads request targets, not the string "http".
- `static/` weighs ≤ 600 KB.
- Every `ReasonCode` has an `.rc-<CODE>` rule in `app.css`, in the hue the doc's colour table gives it, and the
  Stage's coral set in `stage.js` is the same set. The test imports `tower_policy`; the UI may not (`test_static`).
- No number in any picture or file: no run of 10+ digits, no `redact.GROUPED` match, no masked last-four.
- No health word in any text the page shows (visible text only: SVG and CSS say `stroke`).
- A font `LICENSE` (SIL OFL) sits beside the woff2 files.
- Macro bubbles are drawn with the initial "A": that holds only while every `STORIES` entry runs as `user-asish`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from demo_ui.redact import GROUPED
from ref_client.demo import STORIES
from tower_policy.codes import ReasonCode

from tests.privacy.patterns import ALLOWED_PHONE_FORMS, E164_STRICT, HEALTH_WORDS

pytestmark = pytest.mark.unit

SRC = Path(__file__).resolve().parents[1] / "src" / "demo_ui"
STATIC = SRC / "static"
TEMPLATES = SRC / "templates"
WEIGHT_LIMIT = 600_000  # bytes: "page weight ≤ 600 KB" (prompt 21), read strictly as decimal kilobytes

TEXT_SUFFIXES = {".html", ".css", ".js", ".svg"}
SERVED = sorted(
    p
    for d in (STATIC, TEMPLATES)
    for p in d.rglob("*")
    if p.is_file() and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts
)
OURS = [p for p in SERVED if not p.name.startswith("htmx")]  # htmx and its SSE extension are vendored as-is

TARGET = re.compile(
    r"""(?:\b(?:src|href|xlink:href|action|poster|data)\s*=\s*(?P<q>["'])(?P<attr>.*?)(?P=q))"""
    r"""|(?:url\(\s*["']?(?P<url>[^"')]*)["']?\s*\))"""
    r"""|(?:@import\s+(?:url\()?\s*["'](?P<imp>[^"']*)["'])"""
    r"""|(?:\bfetch\(\s*(?P<fetch>[^,)]*))"""
)
JS_CONST = re.compile(r"""const\s+(?P<name>[A-Z_]+)\s*=\s*["'](?P<value>[^"']*)["']""")
SCHEMED = re.compile(r"""(?i)\b(?:https?|wss?|ftp):(?://)?[^\s"'<>)`]+""")
PROTOCOL_RELATIVE = re.compile(r"""[\"'`(]\s*(//[\w.-]+\.[a-z]{2,}[^\s"'<>)`]*)""")
NAMESPACES = {"http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink"}
# A link the binding page draws, opened by the presenter (`<a target=_blank>`), never loaded by the page; its value
# is the binding admin's own bind URL on the laptop (`views.qr_fragment`), never a third party.
DYNAMIC_TARGETS = {("qr.html", "{{ url }}")}


def _targets(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    consts = {m["name"]: m["value"] for m in JS_CONST.finditer(text)} if path.suffix == ".js" else {}
    out = []
    for m in TARGET.finditer(text):
        value = next(v for v in (m["attr"], m["url"], m["imp"], m["fetch"]) if v is not None).strip()
        out.append(consts.get(value, value).strip("\"'`"))
    return out


def _same_origin(target: str) -> bool:
    return target.startswith(("#", "/")) and not target.startswith("//")


def test_every_request_target_is_this_origin() -> None:
    offenders = []
    seen = 0
    for p in OURS:
        for t in _targets(p):
            seen += 1
            if _same_origin(t) or (p.name, t) in DYNAMIC_TARGETS:
                continue
            offenders.append(f"{p.relative_to(SRC)}: {t!r}")
    assert offenders == []
    assert seen > 40  # the scan found the page's real references (scripts, sprite uses, the font, the scene)


def test_no_absolute_url_anywhere_but_the_svg_namespace() -> None:
    """Belt and braces for what `_targets` cannot parse (a URL built in JS, a CSS string): no absolute URL in any
    served text file, vendored htmx included, except the SVG `xmlns`; and no protocol-relative `//host` inside a
    string or `url(` (a bare `//` in JS is a comment)."""
    offenders = set()
    for p in SERVED:
        text = p.read_text(encoding="utf-8")
        offenders |= {f"{p.relative_to(SRC)}: {u}" for u in SCHEMED.findall(text) if u not in NAMESPACES}
        offenders |= {f"{p.relative_to(SRC)}: {u}" for u in PROTOCOL_RELATIVE.findall(text)}
    assert offenders == set()


def test_the_svg_namespace_is_the_only_http_string_in_the_pictures() -> None:
    for svg in sorted((STATIC / "img").glob("*.svg")):
        found = set(re.findall(r"https?://[^\s\"'<>]+", svg.read_text(encoding="utf-8")))
        assert found <= NAMESPACES, (svg.name, found)


def test_static_weighs_at_most_600_kb() -> None:
    total = sum(p.stat().st_size for p in STATIC.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    assert total <= WEIGHT_LIMIT, f"static/ is {total} bytes"


# --- colour classes (doc 11 §2 colour table) ---------------------------------------------------------------------

SOLID_CORAL = {"SIM_SWAPPED_RECENT", "CALL_FORWARDING_SET", "UNREACHABLE", "ALERT_FAILED"}
OUTLINE_CORAL = {"NOT_BOUND", "NO_CONSENT", "STALE_DATA", "CARRIER_ERROR", "SERVICE_UNAVAILABLE"}
HUE = {"OK": "--ok", "ACK_IGNORED_SWAPPED_LINE": "--sms", "SUPPRESSED_REVOKED": "--muted"}
RULE = re.compile(r"(?P<sel>[^{}]+)\{(?P<body>[^{}]*)\}")


def _css() -> str:
    return re.sub(r"/\*.*?\*/", "", (STATIC / "app.css").read_text(encoding="utf-8"), flags=re.S)


def _chip_rules(css: str) -> dict[str, str]:
    """`CODE` → the declarations of every rule whose selector list styles `.rc-CODE > .chip`."""
    out: dict[str, str] = {}
    for m in RULE.finditer(css):
        for sel in m["sel"].split(","):
            code = re.fullmatch(r"\s*\.rc-([A-Z_]+)\s*>\s*\.chip\s*", sel)
            if code:
                out[code.group(1)] = out.get(code.group(1), "") + m["body"]
    return out


def test_every_reason_code_has_a_colour_class() -> None:
    rules = _chip_rules(_css())
    assert {c.value for c in ReasonCode} - set(rules) == set()
    assert set(rules) - {c.value for c in ReasonCode} == set()  # no class for a code Tower does not have


def test_each_code_wears_the_hue_the_doc_gives_it() -> None:
    rules = _chip_rules(_css())
    assert {c.value for c in ReasonCode} == SOLID_CORAL | OUTLINE_CORAL | set(HUE)
    for code in SOLID_CORAL:
        assert re.search(r"background:\s*var\(--alert\)", rules[code]), code
    for code in OUTLINE_CORAL:
        body = rules[code]
        assert re.search(r"color:\s*var\(--alert\)", body), code
        assert re.search(r"background:\s*transparent", body), code
    for code, token in HUE.items():
        assert re.search(rf"color:\s*var\({token}\)", rules[code]), code


def test_the_stage_beacon_turns_coral_on_exactly_the_coral_codes() -> None:
    js = (STATIC / "stage.js").read_text(encoding="utf-8")
    block = re.search(r"const CORAL = new Set\(\[(?P<body>[^\]]*)\]\)", js, re.S)
    assert block, "stage.js lost its CORAL set"
    assert set(re.findall(r'"([A-Z_]+)"', block["body"])) == SOLID_CORAL | OUTLINE_CORAL


def test_an_unknown_code_falls_back_to_a_neutral_chip() -> None:
    base = [m["body"] for m in RULE.finditer(_css()) if re.fullmatch(r"\s*\.chip\s*", m["sel"])]
    assert base and not any("--alert" in b or "--ok" in b for b in base)


# --- privacy on the art and the markup ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [p for p in SERVED], ids=lambda p: str(p.relative_to(SRC)))
def test_no_number_or_number_shaped_string(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert re.findall(r"\d{10,}", text) == []
    assert E164_STRICT.findall(text) == []
    assert GROUPED.findall(text) == []
    assert ALLOWED_PHONE_FORMS.findall(text) == []  # "•••• 0101" is out too (prompt 21 guardrails)


def _visible_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".svg":
        return " ".join(re.findall(r"<(?:text|title|desc|tspan)\b[^>]*>([^<]*)<", text))
    if path.suffix == ".html":
        text = re.sub(r"\{#.*?#\}", " ", text, flags=re.S)
        text = re.sub(r"<[^>]*>", " ", text)
        return text
    if path.suffix == ".css":
        return " ".join(re.findall(r"content:\s*\"([^\"]*)\"", text))
    return " ".join(re.findall(r"""(?:textContent|title)\s*=\s*[`"']([^`"']*)""", text))


@pytest.mark.parametrize("path", OURS, ids=lambda p: str(p.relative_to(SRC)))
def test_no_health_word_in_what_the_page_shows(path: Path) -> None:
    assert HEALTH_WORDS.findall(_visible_text(path)) == []


def test_the_vendored_fonts_carry_their_licence() -> None:
    woff2 = sorted(STATIC.rglob("*.woff2"))
    assert woff2, "no vendored font"
    for folder in {p.parent for p in woff2}:
        licence = folder / "LICENSE"
        assert licence.is_file(), folder
        assert "SIL OPEN FONT LICENSE" in licence.read_text(encoding="utf-8").upper()
    assert not list(STATIC.rglob("*.ttf")) and not list(STATIC.rglob("*.otf"))  # woff2 only


def test_no_raster_image_in_static() -> None:
    raster = [p for p in STATIC.rglob("*") if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}]
    assert raster == []


def test_fragments_carry_no_inline_svg_geometry() -> None:
    """Fragments pass `redact.mask`; inline path data could be mangled into "[number hidden]" (doc 11 §2)."""
    for t in sorted(TEMPLATES.glob("*.html")):
        text = t.read_text(encoding="utf-8")
        assert not re.search(r"<(?:path|polygon|polyline|circle|rect|ellipse|line)\b", text), t.name
        assert ' d="' not in text and "points=" not in text, t.name


# --- the "A" on macro bubbles ------------------------------------------------------------------------------------


def test_every_story_runs_as_asish_so_the_macro_bubble_initial_is_right() -> None:
    assert STORIES and {s.user for s in STORIES} == {"user-asish"}
    turn = (TEMPLATES / "turn.html").read_text(encoding="utf-8")
    assert re.search(r'<span class="avatar"[^>]*>A</span>', turn)  # the assumption this test guards


# --- accessibility rules that live in the markup (doc 11 §2 asset rules) ------------------------------------------


def test_live_regions_landmarks_focus_ring_and_reduced_motion_are_in_place() -> None:
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    for target in ("run-status", "stepper", "announcer"):
        assert re.search(rf'id="{target}"[^>]*aria-live="polite"', page), target
    assert re.search(r'id="stage"[^>]*role="img"[^>]*aria-label="[^"]{20,}"', page)
    assert "<main" in page and "<footer" in page and 'lang="en"' in page
    for pane in ("conversation", "carrier", "feed", "binding"):
        assert re.search(rf'id="pane-{pane}" aria-labelledby="h-{pane}"', page), pane
    css = _css()
    assert re.search(r":focus-visible\s*\{[^}]*outline:\s*2px solid var\(--req\)", css)
    reduced = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(?P<body>.*?)\n\}", css, re.S)
    assert reduced and "animation: none !important" in reduced["body"]
    assert "transition: none !important" in reduced["body"]
    js = (STATIC / "stage.js").read_text(encoding="utf-8")
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js
