#!/usr/bin/env python3
"""Parse the Deliverables blocks of prompts/*.md and report paths that don't exist.

Inside each ``` block under a '## Deliverables' / '## Other deliverables' heading, a line's leading
token(s) are path candidates. Handles: indentation-based nesting under lines ending in '/', brace
expansion {a,b}, comma-separated lists on one line, and skips tokens with '<', '%', '*' or '...'.
Exit 1 if anything is missing; prints the list grouped by prompt.
"""

import itertools
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = sorted((ROOT / "prompts").glob("[0-9][0-9]-*.md"))
SKIP = re.compile(r"[<>%*]|\.\.\.|→|\(|:$|^\.\.")
TOP = (
    "packages/",
    "services/",
    "deploy/",
    "docs/",
    "tests/",
    "scripts/",
    "specs/",
    "scenarios/",
    "artifacts/",
    ".github/",
    "prompts/",
    "mk/",
    "corpus/",
    "README",
    "LICENSE",
    "CONTRIBUTING",
    "SECURITY",
    "Makefile",
    "pyproject",
    ".python",
    ".gitleaks",
    ".gitignore",
    ".pre-commit",
)


def expand(tok: str) -> list[str]:
    m = re.search(r"\{([^}]+)\}", tok)
    if not m:
        return [tok]
    opts = m.group(1).split(",")
    return list(itertools.chain.from_iterable(expand(tok[: m.start()] + o + tok[m.end() :]) for o in opts))


def line_tokens(stripped: str) -> list[str]:
    """First token, or every token of a 'a.py, b.py, c.py' list."""
    head = stripped.split()[0]
    parts = [p.strip() for p in stripped.split(",")]
    if len(parts) > 1 and all(
        " " not in p.split("(")[0].strip() and ("." in p or "/" in p) for p in parts[:-1]
    ):
        return [p.split()[0] for p in parts if p]
    return [head.rstrip(",")]


def candidates(md: str):
    in_deliv = in_code = False
    stack: list[tuple[int, str]] = []
    for line in md.splitlines():
        if line.startswith("## "):
            in_deliv = "deliverables" in line.lower()
            in_code = False
            stack = []
            continue
        if not in_deliv:
            continue
        if line.strip().startswith("```"):
            in_code = not in_code
            stack = []
            continue
        if not in_code or not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else ""
        for tok in line_tokens(line.strip()):
            if SKIP.search(tok) or tok.startswith(("#", "-", "|", "`")):
                continue
            if "/" not in tok and "." not in tok and not tok.startswith(TOP):
                continue
            full = tok if (not stack and tok.startswith(TOP)) or tok.startswith("/") else parent + tok
            if tok.endswith("/"):
                stack.append((indent, full))
            for t in expand(full):
                yield t.rstrip("/")


def main() -> int:
    missing: dict[str, list[str]] = {}
    total = 0
    for p in PROMPTS:
        for c in sorted(set(candidates(p.read_text()))):
            total += 1
            if not (ROOT / c).exists():
                missing.setdefault(p.name, []).append(c)
    n = sum(len(v) for v in missing.values())
    print(f"deliverables: {total} checked, {n} missing")
    for k, v in missing.items():
        print(f"\n{k}")
        for c in v:
            print(f"  - {c}")
    return 0 if n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
