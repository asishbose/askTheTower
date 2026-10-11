#!/usr/bin/env python3
"""Deck ↔ artefacts ↔ README consistency (``make deck-check``).

Reads the pitch deck (``docs/Decks/*.pptx``) with the standard library only: a .pptx is a zip of XML, and
slide text is in ``<a:t>`` runs, speaker notes in ``notesSlideN.xml``. Every check prints one row:
``ok`` / ``OPEN`` with where the two sides disagree. Exit 1 if any row is OPEN.

Checks:
  team        both team members on the cover and close slides, in README.md and docs/submission/form.md
  toolkit     "Alexa+ MCP Toolkit" in the README's first screen, the form description's opening, both Built With lists
  built-with  README "Built with" list == form.md "Built with" list
  tools       tool names on the deck exist in Tower (services/tower-mcp/src)
  apis        CAMARA APIs named on the deck have a vendored spec in specs/camara/
  cost        the deck's "$N for the month" against artifacts/cost.md (estimate range / its own verdict)
  latency     every "N ms" on the deck appears in an artifacts/latency*.md table; README p95 figures likewise
  tests       README test counts / coverage / conformance == artifacts/test-report.md
  dialogue    the user's lines quoted in the README's three moments appear on the deck's demo slide

``--out PATH`` also writes the table as Markdown (prompt 16 owns docs/submission/deck-consistency.md and may
point this at it).
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECKS = ROOT / "docs" / "Decks"
README = ROOT / "README.md"
FORM = ROOT / "docs" / "submission" / "form.md"
ART = ROOT / "artifacts"
TEAM = ("Asish Bose", "Badhrinath Padmanabhan")
TOOLKIT = "Alexa+ MCP Toolkit"
A_T = re.compile(r"<a:t>([^<]*)</a:t>")


@dataclass
class Row:
    check: str
    ok: bool
    detail: str


def _unescape(s: str) -> str:
    return (
        s.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )


def read_deck(path: Path) -> tuple[list[str], list[str]]:
    """Slide texts and notes texts, in slide order."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()

        def num(n: str) -> int:
            return int(re.search(r"(\d+)\.xml$", n).group(1))  # type: ignore[union-attr]

        slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=num)
        out, notes = [], []
        for s in slides:
            out.append(" ".join(_unescape(t) for t in A_T.findall(z.read(s).decode("utf-8"))))
            nn = f"ppt/notesSlides/notesSlide{num(s)}.xml"
            notes.append(
                " ".join(_unescape(t) for t in A_T.findall(z.read(nn).decode("utf-8"))) if nn in names else ""
            )
    return out, notes


def section(md: str, heading: str) -> str:
    m = re.search(rf"^#+ {re.escape(heading)}\s*$(.*?)(?=^#+ |\Z)", md, re.M | re.S)
    return m.group(1) if m else ""


def bullets(text: str) -> list[str]:
    return [ln[2:].strip() for ln in text.splitlines() if ln.startswith("- ")]


def check_team(slides: list[str], readme: str, form: str) -> list[Row]:
    rows = []
    for label, text in (
        ("cover slide", slides[0]),
        ("close slide", slides[-1]),
        ("README", readme),
        ("form.md", form),
    ):
        missing = [n for n in TEAM if n not in text]
        rows.append(
            Row(
                "team",
                not missing,
                f"{label}: " + ("both named" if not missing else f"missing {', '.join(missing)}"),
            )
        )
    return rows


def check_toolkit(readme: str, form: str) -> list[Row]:
    first_screen = "\n".join(readme.splitlines()[:12])
    desc = section(form, "Description")
    opening = " ".join(re.split(r"(?<=[.!?])\s+", re.sub(r"^>\s?", "", desc, flags=re.M).strip())[:2])
    return [
        Row("toolkit", TOOLKIT in first_screen, "README first screen (12 lines)"),
        Row("toolkit", TOOLKIT in opening.replace("**", ""), "form.md description, first two sentences"),
        Row("toolkit", TOOLKIT in bullets(section(readme, "Built with")), "README Built with"),
        Row("toolkit", TOOLKIT in bullets(section(form, "Built with")), "form.md Built with"),
    ]


def check_built_with(readme: str, form: str) -> list[Row]:
    a, b = bullets(section(readme, "Built with")), bullets(section(form, "Built with"))
    diff = sorted(set(a) ^ set(b))
    return [
        Row("built-with", bool(a) and a == b, "README == form.md" if a == b else f"differ: {diff or 'order'}")
    ]


def check_tools(deck: str) -> list[Row]:
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "services/tower-mcp/src").rglob("*.py"))
    names = sorted(set(re.findall(r"\b(line_is_ok|is_reachable|watch_line|line_is_safe_for_otp)\b", deck)))
    return [
        Row("tools", f'"{n}"' in src or f"'{n}'" in src or f"def {n}" in src, f"{n} on the deck → Tower")
        for n in names
    ]


APIS = {
    "SIM Swap": "sim-swap",
    "Number Verification": "number-verification",
    "Call Forwarding Signal": "call-forwarding-signal",
    "Device Reachability": "device-reachability-status",
}


def check_apis(deck: str) -> list[Row]:
    specs = " ".join(p.name for p in (ROOT / "specs/camara").glob("*.y*ml"))
    return [
        Row("apis", stem in specs, f"{api} → specs/camara/{stem}*")
        for api, stem in APIS.items()
        if api in deck
    ]


def check_cost(slides: list[str], notes: list[str]) -> list[Row]:
    cost = (ART / "cost.md").read_text(encoding="utf-8") if (ART / "cost.md").exists() else ""
    rows = []
    for i, text in enumerate(slides, 1):
        for m in re.finditer(r"about \$(\d+)", text):
            n = int(m.group(1))
            ranges = [(int(a), int(b)) for a, b in re.findall(r"≈ \$(\d+)[–-](\d+)/month", cost)]
            verdict_no = bool(re.search(r"does \*\*not\*\* hold", cost))
            inside = any(a <= n <= b for a, b in ranges)
            ok = bool(cost) and inside and not verdict_no
            detail = (
                f"slide {i}: 'about ${n}' for the month vs artifacts/cost.md "
                f"(estimate {', '.join(f'${a}–{b}' for a, b in ranges) or 'none'}/month always-on"
                f"{'; cost.md says the claim does not hold' if verdict_no else ''}; not measured)"
            )
            rows.append(Row("cost", ok, detail))
    return rows or [Row("cost", True, "no monthly cost figure on the deck")]


def _latency_numbers() -> set[str]:
    nums: set[str] = set()
    for p in ART.glob("latency*.md"):
        nums |= set(re.findall(r"\b\d+\.\d\b", p.read_text(encoding="utf-8")))
    return nums


def check_latency(deck: str, readme: str) -> list[Row]:
    have = _latency_numbers()
    rows = [
        Row("latency", n in have, f"deck '{n} ms' in artifacts/latency*.md")
        for n in re.findall(r"(\d+(?:\.\d+)?)\s?ms", deck)
    ]
    if not rows:
        rows.append(Row("latency", True, "no latency figure on the deck ('measured not assumed' only)"))
    meas = section(readme, "What's measured")
    for n in re.findall(r"(\d+\.\d) ms", meas):
        rows.append(Row("latency", n in have, f"README p95 {n} ms in artifacts/latency*.md"))
    return rows


def check_tests(readme: str) -> list[Row]:
    rep_p = ART / "test-report.md"
    if not rep_p.exists():
        return [Row("tests", False, "artifacts/test-report.md missing (make test-report)")]
    rep = rep_p.read_text(encoding="utf-8")
    meas = section(readme, "What's measured")
    rows = []
    for layer, label in (
        ("Unit", "Unit tests"),
        ("Integration", "Integration tests"),
        ("End to end (ENV=local)", "End to end, ENV=local"),
    ):
        m = re.search(rf"^\| {re.escape(layer)} \| (\d+) \| (\d+) \| \d+ \| (\d+) \|", rep, re.M)
        r = re.search(rf"^\| {re.escape(label)} \| (\d+) passed, (\d+) failed, (\d+) skipped", meas, re.M)
        ok = bool(m and r and m.groups() == r.groups())
        rows.append(
            Row(
                "tests",
                ok,
                f"{label}: README {r.groups() if r else '–'} vs report {m.groups() if m else '–'}",
            )
        )
    for pat, label in (
        (r"`packages/` \| \d+ \| \d+ \| ([\d.]+) %", "coverage packages/"),
        (r"`services/` \| \d+ \| \d+ \| ([\d.]+) %", "coverage services/"),
    ):
        m = re.search(pat, rep)
        rows.append(
            Row(
                "tests",
                bool(m and f"{m.group(1)} %" in meas),
                f"{label} {m.group(1) if m else '–'} % in README",
            )
        )
    m = re.search(
        r"(\d+) CAMARA operations, (\d+) generated cases \(schemathesis\), (\d+) operations with failures",
        rep,
    )
    if m:
        want = f"{m.group(1)} operations, {int(m.group(2)):,} generated cases, {m.group(3)} failing"
        rows.append(Row("tests", want in meas, f"conformance '{want}' in README"))
    return rows


def check_dialogue(slides: list[str], readme: str) -> list[Row]:
    demo = next((s for s in slides if "THE DEMO" in s), "")
    lines = re.findall(r'\*\*You:\*\* "([^"]+)"', section(readme, "The problem, in three moments"))
    return [Row("dialogue", ln in demo, f"README '{ln}' on the demo slide") for ln in lines] or [
        Row("dialogue", False, "no dialogue found in README 'The problem, in three moments'")
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Deck ↔ artefacts ↔ README consistency")
    ap.add_argument("--deck", type=Path, help="the .pptx (default: the one in docs/Decks/)")
    ap.add_argument("--out", type=Path, help="also write the table as Markdown here")
    args = ap.parse_args()
    deck_path = args.deck or next(iter(sorted(DECKS.glob("*.pptx"))), None)
    if deck_path is None:
        print("deck-check: no .pptx in docs/Decks/")
        return 1
    slides, notes = read_deck(deck_path)
    deck = " ".join(slides + notes)
    readme = README.read_text(encoding="utf-8") if README.exists() else ""
    form = FORM.read_text(encoding="utf-8") if FORM.exists() else ""
    rows = (
        check_team(slides, readme, form)
        + check_toolkit(readme, form)
        + check_built_with(readme, form)
        + check_tools(deck)
        + check_apis(deck)
        + check_cost(slides, notes)
        + check_latency(deck, readme)
        + check_tests(readme)
        + check_dialogue(slides, readme)
    )
    print(f"deck-check: {deck_path.relative_to(ROOT)} ({len(slides)} slides)")
    for r in rows:
        print(f"  {'ok  ' if r.ok else 'OPEN'}  {r.check:<10} {r.detail}")
    open_rows = [r for r in rows if not r.ok]
    print(f"deck-check: {len(rows)} checks, {len(open_rows)} open")
    if args.out:
        md = ["| Check | Result | Detail |", "|---|---|---|"] + [
            f"| {r.check} | {'ok' if r.ok else 'OPEN'} | {r.detail.replace('|', '/')} |" for r in rows
        ]
        args.out.write_text("\n".join(md) + "\n", encoding="utf-8")
    return 1 if open_rows else 0


if __name__ == "__main__":
    sys.exit(main())
