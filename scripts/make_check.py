#!/usr/bin/env python3
"""`make docs-check`, part 1: the Makefile and the docs agree about target names. Exits non-zero on drift.

Three checks:

1. every `make <target>` mentioned in docs/, prompts/ and README.md exists in the Makefile (rename in the docs,
   not in the Makefile);
2. every target listed in docs/architecture/components/10-scheduler-and-infra.md §5 is defined *and* annotated
   with `## comment`, so `make help` lists it;
3. the README's "Run it" block — between `<!-- make-help:begin -->` and `<!-- make-help:end -->` — matches
   `make help` (plain text). `--write-readme` regenerates it. No markers, no check.

    uv run python scripts/make_check.py
    uv run python scripts/make_check.py --write-readme
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENTION = re.compile(r"`make ([a-z][a-z0-9-]*)")
IGNORE = {"x", "it", "the", "a", "that", "every", "revocation", "testable", "charts"}
DOC10 = ROOT / "docs/architecture/components/10-scheduler-and-infra.md"
MAKEFILES = [ROOT / "Makefile", *sorted((ROOT / "mk").glob("*.mk"))]
ANNOTATED = re.compile(r"^([a-zA-Z0-9_.%-]+):.*?## ", re.M)
BEGIN, END = "<!-- make-help:begin -->", "<!-- make-help:end -->"


def targets() -> set[str]:
    # -r: no built-in rules — their `%:` / `%.o:` patterns would otherwise "match" every mention.
    out = subprocess.run(["make", "-pnr", "help"], cwd=ROOT, capture_output=True, text=True).stdout  # noqa: S607
    found = set(re.findall(r"^([a-zA-Z0-9_.%-]+):(?!=)", out, re.M))
    return {t for t in found if not t.startswith((".", "%"))}


def annotated() -> set[str]:
    return {t for f in MAKEFILES for t in ANNOTATED.findall(f.read_text())}


def matches(name: str, have: set[str]) -> bool:
    # `make showcase-<x>` / `make build-<svc>` stop at the `<`: some target with that prefix must exist.
    if name.endswith("-"):
        return any(h.startswith(name) for h in have)
    return name in have or any(re.fullmatch(p.replace("%", ".+"), name) for p in have if "%" in p)


def contract() -> set[str]:
    """Target names from doc 10 §5's code block (`make a | b [ENV=…]  # comment`; `showcase-<x>` expanded)."""
    text = DOC10.read_text()
    sec = text.split("## 5.", 1)[1].split("\n## ", 1)[0]
    block = sec.split("```", 2)[1]
    names: set[str] = set()
    for line in block.splitlines():
        if not line.startswith("make "):
            continue
        cmd, _, comment = line[5:].partition("#")
        for tok in re.split(r"[|\s]+", re.sub(r"\[[^]]*\]", "", cmd)):
            if not tok:
                continue
            if "<x>" in tok:
                words = comment.split(":", 1)[-1].split()
                names |= {tok.replace("<x>", w) for w in words if re.fullmatch(r"[a-z-]+", w)}
            elif re.search(r"<\w+>", tok):  # `build-<svc>` is the pattern rule `build-%`
                names.add(re.sub(r"<\w+>", "%", tok))
            else:
                names.add(tok)
    return names


def help_text() -> str:
    env = {**os.environ, "NO_COLOR": "1", "ENV": "local"}
    out = subprocess.run(  # noqa: S603
        ["make", "--no-print-directory", "help"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout
    return out.rstrip() + "\n"


def readme_block(write: bool) -> str | None:
    readme = ROOT / "README.md"
    if not readme.exists():
        return None
    text = readme.read_text()
    if BEGIN not in text or END not in text:
        return None
    head, rest = text.split(BEGIN, 1)
    current, tail = rest.split(END, 1)
    want = f"\n```text\n{help_text()}```\n"
    if current == want:
        return None
    if write:
        readme.write_text(head + BEGIN + want + END + tail)
        print("README.md: make-help block regenerated")
        return None
    return "README.md make-help block is stale: uv run python scripts/make_check.py --write-readme"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write-readme", action="store_true", help="regenerate the README make-help block")
    args = ap.parse_args(argv)

    have = targets()
    rc = 0
    bad: dict[str, set[str]] = {}
    for f in [*(ROOT / "docs").rglob("*.md"), *(ROOT / "prompts").glob("*.md"), ROOT / "README.md"]:
        if not f.exists():
            continue
        for m in MENTION.findall(f.read_text(errors="ignore")):
            if m not in IGNORE and not matches(m, have):
                bad.setdefault(f.relative_to(ROOT).as_posix(), set()).add(m)
    if bad:
        rc = 1
        print("unknown make targets referenced:")
        for k, v in sorted(bad.items()):
            print(f"  {k}: {sorted(v)}")

    listed = annotated()
    want = contract()
    missing = sorted(t for t in want if not matches(t, have))
    unlisted = sorted(t for t in want if matches(t, have) and not matches(t, listed))
    if missing or unlisted:
        rc = 1
        print(f"{DOC10.relative_to(ROOT).as_posix()} §5 vs Makefile:")
        if missing:
            print(f"  not defined: {missing}")
        if unlisted:
            print(f"  defined but not in `make help` (no ## comment): {unlisted}")

    stale = readme_block(args.write_readme)
    if stale:
        rc = 1
        print(stale)

    if rc == 0:
        print(f"make targets: ok ({len(have)} defined; {len(want)} from doc 10 §5 all listed in `make help`)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
