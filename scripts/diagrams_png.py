#!/usr/bin/env python3
"""Export docs/architecture/diagrams/*.drawio to docs/architecture/diagrams/png/*.png.

Two exporters, tried in order:

1. The draw.io desktop CLI (``drawio`` or ``draw.io`` on PATH, or ``$DRAWIO``):
   ``drawio --export --format png --border 16 --output <png> <drawio>``.
2. Headless Chromium (Playwright, already a dev dependency for the binding-page tests) rendering the
   file with draw.io's own viewer script, ``viewer-static.min.js`` from github.com/jgraph/drawio.
   The script is cached in ``~/.cache/ask-the-tower/`` (or read from ``$DRAWIO_VIEWER_JS``).

Each PNG is rendered on a white background so it reads the same in GitHub's light and dark themes.
Usage: ``make diagrams-png`` (or ``uv run python scripts/diagrams_png.py [--only 01 02]``).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "architecture" / "diagrams"
OUT = SRC / "png"
VIEWER_URL = "https://raw.githubusercontent.com/jgraph/drawio/dev/src/main/webapp/js/viewer-static.min.js"
CACHE = Path.home() / ".cache" / "ask-the-tower" / "viewer-static.min.js"


def drawio_cli() -> str | None:
    for name in (os.environ.get("DRAWIO", ""), "drawio", "draw.io"):
        if name and shutil.which(name):
            return shutil.which(name)
    return None


def export_cli(cli: str, files: list[Path]) -> None:
    for src in files:
        dst = OUT / f"{src.stem}.png"
        cmd = [cli, "--export", "--format", "png", "--border", "16", "--output", str(dst), str(src)]
        if os.geteuid() == 0:
            cmd.insert(1, "--no-sandbox")
        subprocess.run(cmd, check=True)  # noqa: S603 (fixed argv: the drawio binary and our own paths)
        print(f"diagrams-png: {dst.relative_to(ROOT)} (drawio CLI)")


def viewer_js() -> Path:
    env = os.environ.get("DRAWIO_VIEWER_JS")
    if env:
        return Path(env)
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"diagrams-png: fetching {VIEWER_URL}")
        with urllib.request.urlopen(VIEWER_URL, timeout=120) as r:  # noqa: S310 (fixed https URL)
            CACHE.write_bytes(r.read())
    return CACHE


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;background:#ffffff}} #wrap{{display:inline-block;padding:16px;background:#ffffff}}</style>
</head><body><div id="wrap"><div class="mxgraph" style="background:#ffffff;min-width:10px;min-height:10px" data-mxgraph="{cfg}"></div></div></body></html>"""


def export_browser(files: list[Path]) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit(
            "diagrams-png: no drawio CLI on PATH and Playwright is not installed — "
            "install draw.io desktop (https://github.com/jgraph/drawio-desktop/releases) "
            "or run `uv sync --all-packages && uv run playwright install chromium`"
        )
    js = viewer_js()
    digest = hashlib.sha256(js.read_bytes()).hexdigest()[:12]
    print(f"diagrams-png: draw.io viewer {js} (sha256 {digest}…) in headless Chromium")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=2, viewport={"width": 2400, "height": 1600})
        page.on("pageerror", lambda e: print(f"diagrams-png: page error: {e}", file=sys.stderr))
        for src in files:
            cfg = {
                "xml": src.read_text(encoding="utf-8"),
                "lightbox": False,
                "nav": False,
                "resize": True,
                "toolbar": "",
                "border": 0,
            }
            page.set_content(PAGE.format(cfg=html.escape(json.dumps(cfg), quote=True)))
            page.add_script_tag(path=str(js))
            page.evaluate("GraphViewer.processElements()")
            page.wait_for_selector("#wrap svg", timeout=30_000)
            page.wait_for_timeout(300)
            dst = OUT / f"{src.stem}.png"
            page.locator("#wrap").screenshot(path=str(dst))
            print(f"diagrams-png: {dst.relative_to(ROOT)} (viewer, {dst.stat().st_size // 1024} KB)")
        browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", help="diagram number prefixes, e.g. 01 02")
    ap.add_argument("--browser", action="store_true", help="skip the drawio CLI even if present")
    args = ap.parse_args()
    files = sorted(SRC.glob("*.drawio"))
    if args.only:
        files = [f for f in files if any(f.name.startswith(o) for o in args.only)]
    if not files:
        print("diagrams-png: no .drawio files matched")
        return 1
    OUT.mkdir(exist_ok=True)
    cli = None if args.browser else drawio_cli()
    if cli:
        export_cli(cli, files)
    else:
        export_browser(files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
