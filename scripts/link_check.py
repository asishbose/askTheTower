#!/usr/bin/env python3
"""Relative markdown links under docs/, prompts/ and README.md must resolve."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\((?!https?://|mailto:|#)([^)#]+)(#[^)]*)?\)")


def main() -> int:
    bad = []
    for f in [*(ROOT / "docs").rglob("*.md"), *(ROOT / "prompts").glob("*.md"), ROOT / "README.md"]:
        if not f.exists():
            continue
        for m in LINK.finditer(f.read_text(errors="ignore")):
            target = (f.parent / m.group(1)).resolve()
            if not target.exists():
                bad.append(f"{f.relative_to(ROOT)} -> {m.group(1)}")
    if bad:
        print("broken links:")
        print("\n".join("  " + b for b in bad))
        return 1
    print("links: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
